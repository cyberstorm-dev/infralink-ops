"""Atomic installation of already-authorized bytes at one declared target."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path


class ArtifactTargetError(RuntimeError):
    """A target cannot be safely replaced."""


class ArtifactTargetDurabilityUncertainError(ArtifactTargetError):
    """The target was replaced but its parent directory was not durably synced."""


@dataclass(frozen=True)
class ArtifactTargetResult:
    changed: bool


def _open_directory(path: Path) -> int:
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(component in {"", ".", ".."} for component in path.parts[1:])
    ):
        raise ArtifactTargetError("artifact directory is unsafe")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:]:
            successor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = successor
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise ArtifactTargetError("artifact directory is unsafe") from error


def _read_regular(parent_fd: int, name: str) -> tuple[bytes, os.stat_result]:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks), details
    except (OSError, ValueError) as error:
        raise ArtifactTargetError("artifact target inspection failed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    offset = 0
    while offset < len(body):
        written = os.write(descriptor, body[offset:])
        if written <= 0:
            raise OSError("artifact write made no progress")
        offset += written


def install_artifact_body(
    target: Path, body: bytes, *, mode: int, uid: int, gid: int
) -> ArtifactTargetResult:
    """Atomically replace one explicit regular-file target without retaining input bytes."""
    if not isinstance(target, Path) or not target.is_absolute() or type(body) is not bytes:
        raise ArtifactTargetError("artifact install inputs are invalid")
    if any(type(value) is not int or value < 0 for value in (mode, uid, gid)) or mode & ~0o777:
        raise ArtifactTargetError("artifact target metadata is invalid")
    parent_fd = _open_directory(target.parent)
    temporary: str | None = None
    committed = False
    try:
        try:
            details = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(details.st_mode):
                directory_fd = os.open(
                    target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                )
                try:
                    if os.listdir(directory_fd):
                        raise ArtifactTargetError("managed_destination_nonempty_directory")
                finally:
                    os.close(directory_fd)
                os.rmdir(target.name, dir_fd=parent_fd)
            elif not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
                raise ArtifactTargetError("managed_destination_symlink")
            else:
                current_body, current = _read_regular(parent_fd, target.name)
                if (
                    current_body == body
                    and stat.S_IMODE(current.st_mode) == mode
                    and current.st_uid == uid
                    and current.st_gid == gid
                ):
                    os.fsync(parent_fd)
                    return ArtifactTargetResult(changed=False)
        except FileNotFoundError:
            pass
        temporary = f".{target.name}.{secrets.token_hex(16)}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600, dir_fd=parent_fd
        )
        try:
            _write_all(descriptor, body)
            os.fchmod(descriptor, mode)
            os.fchown(descriptor, uid, gid)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
        committed = True
        os.fsync(parent_fd)
        return ArtifactTargetResult(changed=True)
    except ArtifactTargetError:
        raise
    except OSError as error:
        if committed:
            raise ArtifactTargetDurabilityUncertainError(
                "artifact target durability uncertain"
            ) from error
        raise ArtifactTargetError("artifact target installation failed") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)
