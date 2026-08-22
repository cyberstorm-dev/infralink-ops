"""Materialize explicit controller-owned runtime directories."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "infralink.ops.runtime-directories/v1"
ALLOWED_ROOTS = ("/var/lib/", "/var/log/", "/run/")


class RuntimeDirectoryError(ValueError):
    """One declared runtime-directory request is invalid or cannot be applied."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid CLI usage in the machine-readable response."""

    def error(self, message: str) -> None:
        raise RuntimeDirectoryError("usage_error")


@dataclass(frozen=True)
class RuntimeDirectory:
    """One explicitly managed directory below an allowed host root."""

    path: str
    mode: int
    owner_uid: int
    owner_gid: int

    def document(self, *, exists: bool) -> dict[str, object]:
        return {
            "path": self.path,
            "mode": f"{self.mode:04o}",
            "owner_uid": self.owner_uid,
            "owner_gid": self.owner_gid,
            "exists": exists,
        }


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeDirectoryError("runtime_directories_invalid")
    return value


def _mode(value: object) -> int:
    if (
        not isinstance(value, str)
        or len(value) != 4
        or value[0] != "0"
        or any(character not in "01234567" for character in value)
    ):
        raise RuntimeDirectoryError("runtime_directory_mode_invalid")
    return int(value, 8)


def _owner(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeDirectoryError("runtime_directory_owner_invalid")
    return value


def _directory(value: object) -> RuntimeDirectory:
    document = _mapping(value)
    if set(document) != {"path", "mode", "owner_uid", "owner_gid"}:
        raise RuntimeDirectoryError("runtime_directories_invalid")
    path = document["path"]
    if (
        not isinstance(path, str)
        or not any(path.startswith(root) for root in ALLOWED_ROOTS)
        or path.endswith("/")
        or "/../" in f"/{path.removeprefix('/')}"
        or path in {root.removesuffix("/") for root in ALLOWED_ROOTS}
    ):
        raise RuntimeDirectoryError("runtime_directory_path_not_allowed")
    return RuntimeDirectory(
        path=path,
        mode=_mode(document["mode"]),
        owner_uid=_owner(document["owner_uid"]),
        owner_gid=_owner(document["owner_gid"]),
    )


def _directories(deployment: Path) -> tuple[RuntimeDirectory, ...]:
    try:
        document = _mapping(yaml.safe_load(deployment.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeDirectoryError("runtime_directories_unavailable") from error
    values = document.get("runtime_directories", [])
    if not isinstance(values, list):
        raise RuntimeDirectoryError("runtime_directories_invalid")
    directories = tuple(_directory(value) for value in values)
    if len({directory.path for directory in directories}) != len(directories):
        raise RuntimeDirectoryError("runtime_directory_duplicate")
    return directories


def _host_root(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise RuntimeDirectoryError("host_root_invalid")
    return path.resolve()


def _destination(host_root: Path, path: str) -> Path:
    return host_root / path.removeprefix("/")


def _ensure_safe_ancestors(path: Path, *, host_root: Path) -> None:
    current = host_root
    for part in path.relative_to(host_root).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeDirectoryError("runtime_directory_symlink_unsafe")
            if not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeDirectoryError("runtime_directory_not_directory")


def _preflight(host_root: Path, directories: tuple[RuntimeDirectory, ...]) -> None:
    for directory in directories:
        _ensure_safe_ancestors(_destination(host_root, directory.path), host_root=host_root)


def _materialize(host_root: Path, directories: tuple[RuntimeDirectory, ...]) -> None:
    for directory in directories:
        destination = _destination(host_root, directory.path)
        _ensure_safe_ancestors(destination, host_root=host_root)
        destination.mkdir(parents=True, exist_ok=True)
        metadata = destination.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeDirectoryError("runtime_directory_not_directory")
        os.chmod(destination, directory.mode)
        os.chown(destination, directory.owner_uid, directory.owner_gid)


def _exists(destination: Path) -> bool:
    if not destination.exists() and not destination.is_symlink():
        return False
    return stat.S_ISDIR(destination.lstat().st_mode)


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


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Plan or apply one explicit runtime-directory declaration document."""

    parser = EnvelopeParser(prog="infralink-controller-runtime-directories")
    parser.add_argument("mode", choices=("plan", "apply"))
    parser.add_argument("--deployment", required=True, type=Path)
    parser.add_argument("--host-root", required=True, type=Path)
    try:
        arguments = parser.parse_args(argv)
    except RuntimeDirectoryError as error:
        return _payload(command=None, error=str(error)), 64
    try:
        host_root = _host_root(arguments.host_root)
        directories = _directories(arguments.deployment)
        _preflight(host_root, directories)
        if arguments.mode == "apply":
            _materialize(host_root, directories)
        return _payload(
            command=arguments.mode,
            result={
                "directories": [
                    directory.document(exists=_exists(_destination(host_root, directory.path)))
                    for directory in directories
                ]
            },
        ), 0
    except RuntimeDirectoryError as error:
        return _payload(command=arguments.mode, error=str(error)), 78
    except (OSError, OverflowError):
        return _payload(command=arguments.mode, error="runtime_directory_apply_failed"), 78


def cli() -> int:
    """Write one YAML runtime-directory response envelope."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
