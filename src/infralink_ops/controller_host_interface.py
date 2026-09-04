"""Refresh the fixed host interface owned by the controller image."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infralink_ops.host_interface_assets import asset_path

SCHEMA_VERSION = "infralink.ops.host-interface/v1"


class HostInterfaceError(ValueError):
    """The fixed controller-owned host interface cannot be refreshed safely."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid usage inside the machine-readable response envelope."""

    def error(self, message: str) -> None:
        raise HostInterfaceError("usage_error")


@dataclass(frozen=True)
class HostInterfaceAsset:
    """One fixed, controller-owned executable or unit file."""

    source_name: str
    destination: str
    mode: int

    def document(self) -> dict[str, str]:
        return {"path": self.destination, "mode": f"{self.mode:04o}"}


ASSETS = (
    HostInterfaceAsset("infralink", "/usr/local/bin/infralink", 0o755),
    HostInterfaceAsset("infralink-runtime", "/usr/libexec/infralink/runtime", 0o755),
    HostInterfaceAsset(
        "infralink-host-reconcile.service",
        "/etc/systemd/system/infralink-host-reconcile.service",
        0o644,
    ),
    HostInterfaceAsset(
        "infralink-host-reconcile.timer",
        "/etc/systemd/system/infralink-host-reconcile.timer",
        0o644,
    ),
)
SYSTEMD_UNIT_ASSET_NAMES = frozenset(
    {"infralink-host-reconcile.service", "infralink-host-reconcile.timer"}
)
RETIRED_DESTINATIONS = ("/usr/local/sbin/infralink-host",)


def _payload(
    *, command: str | None, result: dict[str, Any] | None = None, error: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command else []},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = {"code": error}
    return payload


def _host_root(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise HostInterfaceError("host_interface_root_invalid")
    return path.resolve()


def _destination(host_root: Path, asset: HostInterfaceAsset) -> Path:
    return host_root / asset.destination.removeprefix("/")


def _ensure_safe_parent(destination: Path, *, host_root: Path, create: bool) -> None:
    current = host_root
    for part in destination.relative_to(host_root).parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise HostInterfaceError("host_interface_path_unsafe")
        elif create:
            current.mkdir()


def _target_matches(destination: Path, *, contents: bytes, mode: int) -> bool:
    if not destination.exists() and not destination.is_symlink():
        return False
    metadata = destination.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HostInterfaceError("host_interface_path_unsafe")
    return destination.read_bytes() == contents and stat.S_IMODE(metadata.st_mode) == mode


def _snapshot(destination: Path) -> tuple[bytes, int] | None:
    if not destination.exists() and not destination.is_symlink():
        return None
    metadata = destination.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HostInterfaceError("host_interface_path_unsafe")
    return destination.read_bytes(), stat.S_IMODE(metadata.st_mode)


def _retired_destinations(host_root: Path) -> tuple[Path, ...]:
    """Validate former public paths before the replacement unit takes effect."""

    destinations = tuple(host_root / path.removeprefix("/") for path in RETIRED_DESTINATIONS)
    for destination in destinations:
        _ensure_safe_parent(destination, host_root=host_root, create=False)
        _snapshot(destination)
    return destinations


def _write_atomically(destination: Path, *, contents: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except OSError as error:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise HostInterfaceError("host_interface_refresh_failed") from error


def _restore_unit(destination: Path, snapshot: tuple[bytes, int] | None) -> None:
    try:
        if snapshot is None:
            destination.unlink(missing_ok=True)
            return
        contents, mode = snapshot
        _write_atomically(destination, contents=contents, mode=mode)
    except OSError as error:
        raise HostInterfaceError("host_interface_refresh_failed") from error


def refresh(host_root: Path) -> dict[str, object]:
    """Atomically materialize the exact packaged launcher and systemd units."""

    sources: list[tuple[HostInterfaceAsset, Path, bytes]] = []
    unit_snapshots: dict[Path, tuple[bytes, int] | None] = {}
    retired_destinations = _retired_destinations(host_root)
    for asset in ASSETS:
        destination = _destination(host_root, asset)
        _ensure_safe_parent(destination, host_root=host_root, create=False)
        try:
            contents = asset_path(asset.source_name).read_bytes()
        except OSError as error:
            raise HostInterfaceError("host_interface_refresh_failed") from error
        _target_matches(destination, contents=contents, mode=asset.mode)
        sources.append((asset, destination, contents))
        if asset.source_name in SYSTEMD_UNIT_ASSET_NAMES:
            unit_snapshots[destination] = _snapshot(destination)

    changed_assets: set[str] = set()
    for asset, destination, contents in sources:
        _ensure_safe_parent(destination, host_root=host_root, create=True)
        if not _target_matches(destination, contents=contents, mode=asset.mode):
            _write_atomically(destination, contents=contents, mode=asset.mode)
            changed_assets.add(asset.source_name)

    systemd_reloaded = False
    changed_unit_destinations = [
        destination
        for asset, destination, _contents in sources
        if asset.source_name in SYSTEMD_UNIT_ASSET_NAMES and asset.source_name in changed_assets
    ]
    if changed_unit_destinations:
        try:
            subprocess.run(
                [
                    "nsenter",
                    "--target",
                    "1",
                    "--mount",
                    "--pid",
                    "--",
                    "systemctl",
                    "daemon-reload",
                ],
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            for destination in changed_unit_destinations:
                _restore_unit(destination, unit_snapshots[destination])
            raise HostInterfaceError("host_interface_systemd_reload_failed") from error
        systemd_reloaded = True
    retired_assets: list[str] = []
    for destination in retired_destinations:
        if destination.exists() or destination.is_symlink():
            try:
                destination.unlink()
            except OSError as error:
                raise HostInterfaceError("host_interface_retire_failed") from error
            retired_assets.append("/" + str(destination.relative_to(host_root)))
    return {
        "changed": bool(changed_assets or retired_assets),
        "systemd_reloaded": systemd_reloaded,
        "retired_assets": retired_assets,
        "assets": [asset.document() for asset in ASSETS],
    }


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Refresh the controller-owned host interface and return one YAML envelope."""

    parser = EnvelopeParser(prog="infralink-controller-host-interface")
    parser.add_argument("command", choices=("refresh",))
    parser.add_argument("--host-root", required=True, type=Path)
    try:
        arguments = parser.parse_args(argv)
    except HostInterfaceError as error:
        return _payload(command=None, error=str(error)), 64
    try:
        result = refresh(_host_root(arguments.host_root))
        return _payload(command=arguments.command, result=result), 0
    except HostInterfaceError as error:
        return _payload(command=arguments.command, error=str(error)), 78
    except OSError:
        return _payload(command=arguments.command, error="host_interface_refresh_failed"), 78


def cli() -> int:
    """Write the host-interface refresh response as YAML."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
