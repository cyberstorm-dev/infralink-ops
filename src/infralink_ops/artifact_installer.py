"""Validated materialization of one registry-declared generated artifact."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from infralink_ops.declared_file_destination import (
    DeclaredFileDestinationError,
    repair_empty_declared_file_destination,
)
from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file


class ArtifactInstallError(ValueError):
    """An artifact declaration cannot be materialized safely."""


@dataclass(frozen=True)
class DeclaredArtifact:
    body: bytes
    source_path: str
    sha256: str


@dataclass(frozen=True)
class ArtifactTarget:
    destination: Path
    mode: int
    owner_uid: int
    owner_gid: int


def read_declared_artifact(registry: Path, declaration: Mapping[str, object]) -> DeclaredArtifact:
    """Read exact source bytes from a caller-selected registry checkout."""

    source = _mapping(declaration.get("source"), "declared artifact source is malformed")
    path, expected = source.get("path"), source.get("sha256")
    if not isinstance(path, str) or not isinstance(expected, str) or len(expected) != 64:
        raise ArtifactInstallError("declared artifact source is malformed")
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ArtifactInstallError("declared artifact source is unavailable")
    try:
        body = read_stable_regular_file(registry.joinpath(*relative.parts))
    except StableRegularFileError as error:
        raise ArtifactInstallError("declared artifact source is unavailable") from error
    if hashlib.sha256(body).hexdigest() != expected:
        raise ArtifactInstallError("declared artifact source digest mismatch")
    return DeclaredArtifact(body=body, source_path=path, sha256=expected)


def resolve_declared_artifact_target(
    services_dir: Path, declaration: Mapping[str, object]
) -> ArtifactTarget:
    """Resolve one explicit ``/opt/services`` artifact target without writing it."""

    target = _mapping(declaration.get("target"), "declared artifact target is malformed")
    path = target.get("path")
    mode = target.get("mode", "0644")
    uid = target.get("owner_uid", 0)
    gid = target.get("owner_gid", 0)
    if (
        not isinstance(path, str)
        or not path.startswith("/opt/services/")
        or not isinstance(mode, str)
        or not isinstance(uid, int)
        or isinstance(uid, bool)
        or not isinstance(gid, int)
        or isinstance(gid, bool)
    ):
        raise ArtifactInstallError("declared artifact target is malformed")
    relative = Path(path.removeprefix("/opt/services/"))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ArtifactInstallError("declared artifact target escapes services directory")
    try:
        parsed_mode = int(mode, 8)
    except ValueError as error:
        raise ArtifactInstallError("declared artifact target mode is malformed") from error
    return ArtifactTarget(services_dir.joinpath(*relative.parts), parsed_mode, uid, gid)


def install_declared_artifact(
    body: bytes, target: ArtifactTarget, *, config_root: Path | None = None
) -> bool:
    """Atomically install exact bytes and declared metadata; return content change."""

    destination = target.destination
    if config_root is not None:
        try:
            destination = repair_empty_declared_file_destination(
                config_root, destination.relative_to(config_root)
            )
        except (DeclaredFileDestinationError, ValueError) as error:
            raise ArtifactInstallError(str(error)) from error
    existing = destination.read_bytes() if destination.is_file() else None
    changed = existing != body
    if changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            stream.write(body)
            staged = Path(stream.name)
        try:
            os.chmod(staged, target.mode)
            os.chown(staged, target.owner_uid, target.owner_gid)
            staged.replace(destination)
        finally:
            if staged.exists():
                staged.unlink()
    elif destination.is_file():
        metadata = destination.stat()
        if metadata.st_mode & 0o7777 != target.mode:
            os.chmod(destination, target.mode)
        if metadata.st_uid != target.owner_uid or metadata.st_gid != target.owner_gid:
            os.chown(destination, target.owner_uid, target.owner_gid)
    return changed


def _mapping(value: Any, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ArtifactInstallError(message)
    return value
