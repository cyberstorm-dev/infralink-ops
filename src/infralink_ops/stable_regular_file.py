"""Read one absolute regular file without following path components."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class StableRegularFileError(RuntimeError):
    """A file could not be read as one stable regular file."""


def _open_directory(path: Path) -> int:
    if (
        not path.is_absolute()
        or path == Path("/")
        or any(component in {"", ".", ".."} for component in path.parts[1:])
    ):
        raise StableRegularFileError("stable regular file path is unsafe")
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
        raise StableRegularFileError("stable regular file path is unsafe") from error


def read_stable_regular_file(path: Path) -> bytes:
    """Return an exact stable regular-file body from an absolute path."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise StableRegularFileError("stable regular file path is unsafe")
    parent_fd = _open_directory(path.parent)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise ValueError
        return b"".join(chunks)
    except (OSError, ValueError) as error:
        raise StableRegularFileError("source is not a stable regular file") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)
