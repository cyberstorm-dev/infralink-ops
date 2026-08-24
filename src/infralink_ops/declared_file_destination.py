"""Validate and repair one explicitly declared controller-owned file destination."""

from __future__ import annotations

from pathlib import Path, PurePath


class DeclaredFileDestinationError(ValueError):
    """A declared runtime file path is incompatible with safe replacement."""


def _relative_path(relative: Path) -> Path:
    if (
        not relative.parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise DeclaredFileDestinationError("managed_destination_invalid: invalid-relative-path")
    return relative


def classify_declared_file_destination(root: Path, relative: Path) -> Path:
    """Return an explicitly declared file path after non-mutating type checks.

    Existing parents below ``root`` must be real directories. The leaf may be
    absent, a regular file, or an empty directory eligible for explicit repair.
    """
    relative = _relative_path(PurePath(relative))
    parent = root
    if parent.is_symlink():
        raise DeclaredFileDestinationError("managed_destination_parent_symlink: .")
    if parent.exists() and not parent.is_dir():
        raise DeclaredFileDestinationError("managed_destination_parent_not_directory: .")
    for component in relative.parts[:-1]:
        parent /= component
        label = parent.relative_to(root).as_posix()
        if parent.is_symlink():
            raise DeclaredFileDestinationError(f"managed_destination_parent_symlink: {label}")
        if parent.exists() and not parent.is_dir():
            raise DeclaredFileDestinationError(f"managed_destination_parent_not_directory: {label}")
    destination = root.joinpath(*relative.parts)
    label = relative.as_posix()
    if destination.is_symlink():
        raise DeclaredFileDestinationError(f"managed_destination_symlink: {label}")
    if destination.is_dir():
        try:
            next(destination.iterdir())
        except StopIteration:
            return destination
        raise DeclaredFileDestinationError(f"managed_destination_nonempty_directory: {label}")
    if destination.exists() and not destination.is_file():
        raise DeclaredFileDestinationError(f"managed_destination_invalid: {label}")
    return destination


def repair_empty_declared_file_destination(root: Path, relative: Path) -> Path:
    """Remove only an empty directory at an explicitly declared file destination."""
    destination = classify_declared_file_destination(root, relative)
    if destination.is_dir():
        destination.rmdir()
    return destination
