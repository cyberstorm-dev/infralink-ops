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
LEGACY_V2_RECONCILE_DESTINATIONS = (
    "/etc/systemd/system/self-deploy-v2-reconcile.service",
    "/etc/systemd/system/self-deploy-v2-reconcile.service.d/environment.conf",
    "/etc/systemd/system/self-deploy-v2-reconcile.timer",
    "/usr/local/sbin/self-deploy-v2-reconcile",
)
LEGACY_V2_RECONCILE_UNITS = (
    "self-deploy-v2-reconcile.service",
    "self-deploy-v2-reconcile.timer",
)


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

    destinations = tuple(
        host_root / path.removeprefix("/")
        for path in (*RETIRED_DESTINATIONS, *LEGACY_V2_RECONCILE_DESTINATIONS)
    )
    for destination in destinations:
        _ensure_safe_parent(destination, host_root=host_root, create=False)
        _snapshot(destination)
    return destinations


def _legacy_v2_reconcile_is_inactive() -> None:
    """Refuse to replace a deployment path that could still apply state."""

    for unit in LEGACY_V2_RECONCILE_UNITS:
        if _systemd_unit_is_active(unit):
            raise HostInterfaceError("legacy_reconcile_active")
    if _systemd_unit_is_enabled("self-deploy-v2-reconcile.timer"):
        raise HostInterfaceError("legacy_reconcile_active")


def _canonical_reconcile_timer_is_ready() -> bool:
    """Whether a staged interface has a future canonical reconciliation path."""

    return _systemd_unit_is_active("infralink-host-reconcile.timer") and _systemd_unit_is_enabled(
        "infralink-host-reconcile.timer"
    )


def _disable_legacy_v2_reconcile_timer() -> None:
    """Stop future legacy reconciles without interrupting the parent service."""

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
                "disable",
                "--now",
                "self-deploy-v2-reconcile.timer",
            ],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HostInterfaceError("legacy_reconcile_timer_disable_failed") from error


def _systemd_unit_is_active(unit: str) -> bool:
    """Read a unit's active state from the host namespace."""

    return _systemd_unit_has_state("is-active", unit, inactive_codes=(3, 4))


def _systemd_unit_is_enabled(unit: str) -> bool:
    """Read a unit's enabled state from the host namespace."""

    return _systemd_unit_has_state("is-enabled", unit, inactive_codes=(1, 3, 4))


def _systemd_unit_has_state(action: str, unit: str, *, inactive_codes: tuple[int, ...]) -> bool:
    try:
        result = subprocess.run(
            [
                "nsenter",
                "--target",
                "1",
                "--mount",
                "--pid",
                "--",
                "systemctl",
                action,
                "--quiet",
                unit,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise HostInterfaceError("legacy_reconcile_state_unavailable") from error
    if result.returncode == 0:
        return True
    if result.returncode in inactive_codes:
        return False
    raise HostInterfaceError("legacy_reconcile_state_unavailable")


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


def refresh(
    host_root: Path, *, retire_legacy: bool = True, require_canonical_timer: bool = False
) -> dict[str, object]:
    """Atomically materialize the exact packaged launcher and systemd units."""

    sources: list[tuple[HostInterfaceAsset, Path, bytes]] = []
    unit_snapshots: dict[Path, tuple[bytes, int] | None] = {}
    if require_canonical_timer and not _canonical_reconcile_timer_is_ready():
        raise HostInterfaceError("canonical_reconcile_timer_required")
    retired_destinations = _retired_destinations(host_root) if retire_legacy else ()
    if retire_legacy:
        _legacy_v2_reconcile_is_inactive()
    retired_snapshots = {
        destination: _snapshot(destination) for destination in retired_destinations
    }
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

    retired_assets: list[str] = []
    retired_systemd_destinations: list[Path] = []
    try:
        for destination in retired_destinations:
            if retired_snapshots[destination] is None:
                continue
            destination.unlink()
            retired_assets.append("/" + str(destination.relative_to(host_root)))
            if destination.is_relative_to(host_root / "etc/systemd/system"):
                retired_systemd_destinations.append(destination)
    except OSError as error:
        for retired_destination in reversed(retired_destinations):
            if retired_destination in retired_snapshots:
                _restore_unit(retired_destination, retired_snapshots[retired_destination])
        raise HostInterfaceError("host_interface_retire_failed") from error

    systemd_reloaded = False
    changed_unit_destinations = [
        destination
        for asset, destination, _contents in sources
        if asset.source_name in SYSTEMD_UNIT_ASSET_NAMES and asset.source_name in changed_assets
    ]
    if changed_unit_destinations or retired_systemd_destinations:
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
            for destination in retired_systemd_destinations:
                _restore_unit(destination, retired_snapshots[destination])
            for destination in retired_destinations:
                if destination not in retired_systemd_destinations:
                    _restore_unit(destination, retired_snapshots[destination])
            raise HostInterfaceError("host_interface_systemd_reload_failed") from error
        systemd_reloaded = True
    return {
        "changed": bool(changed_assets or retired_assets),
        "systemd_reloaded": systemd_reloaded,
        "retired_assets": retired_assets,
        "assets": [asset.document() for asset in ASSETS],
    }


def stage(host_root: Path) -> dict[str, object]:
    """Install the canonical interface without retiring the active seed path.

    The seed-to-selected-controller handoff uses this exactly once.  A later
    canonical timer reconcile calls ``refresh`` and performs guarded retirement.
    """

    result = refresh(host_root, retire_legacy=False, require_canonical_timer=True)
    if not _canonical_reconcile_timer_is_ready():
        raise HostInterfaceError("canonical_reconcile_timer_required")
    _disable_legacy_v2_reconcile_timer()
    return result


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
