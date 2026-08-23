"""Revision-verified materialization of registry-declared static config trees."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_DECLARED_CONFIG_ROOT = PurePosixPath("/opt/services/config")


@dataclass(frozen=True)
class ConfigTreeResult:
    """The file paths changed beneath the controller-owned config root."""

    changed_paths: tuple[str, ...]


def preflight_config_trees(
    registry_root: Path,
    *,
    expected_revision: str,
    declarations: Sequence[Mapping[str, Any]],
    services_root: Path = Path("/opt/services"),
) -> None:
    """Validate a complete declared tree collection before any tree is written.

    Targets are exclusive.  Allowing one declared tree beneath another would
    make stale-file cleanup from either tree destructive to the other.
    """

    root = _verified_registry_root(registry_root, expected_revision)
    declared_targets: list[PurePosixPath] = []
    for declaration in declarations:
        if not isinstance(declaration, Mapping):
            raise ValueError("declared config tree must be a mapping")
        source = _declared_source_directory(root, declaration.get("source"))
        target, relative_target = _declared_target_directory(
            services_root, declaration.get("target")
        )
        _metadata(declaration)
        source_files, source_directories = _preflight_source_tree(
            source, _tracked_source_files(root, expected_revision, source)
        )
        _preflight_target_tree(target, source_files, source_directories)
        for existing_target in declared_targets:
            if _targets_overlap(existing_target, relative_target):
                raise ValueError("declared config tree targets must not overlap")
        declared_targets.append(relative_target)


def materialize_config_tree(
    registry_root: Path,
    *,
    expected_revision: str,
    declaration: Mapping[str, Any],
    services_root: Path = Path("/opt/services"),
) -> ConfigTreeResult:
    """Synchronize one declared static tree from an exact registry checkout.

    This function neither fetches nor selects a registry revision. Callers must
    provide the checkout selected by their normal deployment path and its exact
    expected Git revision.
    """

    root = _verified_registry_root(registry_root, expected_revision)
    source = _declared_source_directory(root, declaration.get("source"))
    target, relative_target = _declared_target_directory(services_root, declaration.get("target"))
    metadata = _metadata(declaration)
    source_files, source_directories = _preflight_source_tree(
        source, _tracked_source_files(root, expected_revision, source)
    )
    _preflight_target_tree(target, source_files, source_directories)
    return _synchronize_tree(
        source,
        target,
        relative_target=relative_target,
        source_files=source_files,
        source_directories=source_directories,
        metadata=metadata,
    )


def _targets_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return (
        left.parts == right.parts[: len(left.parts)]
        or right.parts == left.parts[: len(right.parts)]
    )


def _verified_registry_root(registry_root: Path, expected_revision: str) -> Path:
    root = registry_root.resolve()
    if not root.is_dir():
        raise ValueError(f"registry root must be a directory: {registry_root}")
    if _git_toplevel(root) != root:
        raise ValueError(f"registry root must be the Git checkout top-level: {registry_root}")
    actual_revision = _git_revision(root)
    if actual_revision != expected_revision:
        raise ValueError(
            "registry revision mismatch: "
            f"expected {expected_revision}, checkout has {actual_revision}"
        )
    if _git_status(root):
        raise ValueError(f"registry checkout must be clean: {registry_root}")
    return root


def _git_toplevel(root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git top-level: {root}")
    return Path(completed.stdout.strip()).resolve()


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git HEAD: {root}")
    return completed.stdout.strip()


def _git_status(root: Path) -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git status: {root}")
    return completed.stdout


def _declared_source_directory(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source must be a non-empty relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"source must be a directory below registry root: {value}")
    source = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            raise ValueError(f"source must be a directory below registry root: {value}") from None
        if stat.S_ISLNK(current_stat.st_mode):
            if current == source:
                raise ValueError(f"source must not be a symlink: {value}")
            raise ValueError(f"source must not traverse a symlink: {value}")
    if not source.is_dir():
        raise ValueError(f"source must be a directory below registry root: {value}")
    return source


def _tracked_source_files(
    root: Path, expected_revision: str, source: Path
) -> tuple[PurePosixPath, ...]:
    source_relative = PurePosixPath(source.relative_to(root).as_posix())
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            expected_revision,
            "--",
            source_relative.as_posix(),
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry revision cannot inventory declared source: {source_relative}")
    return tuple(
        PurePosixPath(path.decode("utf-8")).relative_to(source_relative)
        for path in completed.stdout.split(b"\0")
        if path
    )


def _declared_target_directory(services_root: Path, value: Any) -> tuple[Path, PurePosixPath]:
    if not isinstance(value, str) or not value:
        raise ValueError("target must be a non-empty path below /opt/services/config")
    declared = PurePosixPath(value)
    try:
        relative = declared.relative_to(_DECLARED_CONFIG_ROOT)
    except ValueError as error:
        raise ValueError(f"target must be below /opt/services/config: {value}") from error
    if not relative.parts or ".." in relative.parts:
        raise ValueError(f"target must be below /opt/services/config: {value}")
    target = Path(services_root) / "config" / Path(*relative.parts)
    return target, relative


def _metadata(declaration: Mapping[str, Any]) -> tuple[int, int, int, int]:
    file_mode = _octal_mode(declaration.get("file_mode", "0644"), "file_mode")
    directory_mode = _octal_mode(declaration.get("directory_mode", "0755"), "directory_mode")
    uid = _nonnegative_integer(declaration.get("owner_uid"), "owner_uid")
    gid = _nonnegative_integer(declaration.get("owner_gid"), "owner_gid")
    return file_mode, directory_mode, uid, gid


def _octal_mode(value: Any, name: str) -> int:
    valid_digits = isinstance(value, str) and all(char in "01234567" for char in value)
    if not valid_digits or len(value) != 4 or value[0] != "0":
        raise ValueError(f"{name} must be a four-digit octal string without special bits")
    return int(value, 8)


def _nonnegative_integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _preflight_source_tree(
    source: Path,
    tracked_files: tuple[PurePosixPath, ...],
) -> tuple[tuple[PurePosixPath, ...], tuple[PurePosixPath, ...]]:
    files: list[PurePosixPath] = []
    directories: list[PurePosixPath] = [PurePosixPath(".")]
    expected_files = set(tracked_files)
    expected_directories = {PurePosixPath(".")}
    for file_path in tracked_files:
        expected_directories.update(file_path.parents)
    for path in sorted(source.rglob("*")):
        relative = PurePosixPath(path.relative_to(source).as_posix())
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            raise ValueError(f"source tree contains a symlink: {relative}")
        if stat.S_ISDIR(path_stat.st_mode):
            if relative not in expected_directories:
                raise ValueError("source tree must match tracked registry content")
            directories.append(relative)
        elif stat.S_ISREG(path_stat.st_mode):
            if relative not in expected_files:
                raise ValueError("source tree must match tracked registry content")
            files.append(relative)
        else:
            raise ValueError(f"source tree contains an unsupported file type: {relative}")
    if set(files) != expected_files or set(directories) != expected_directories:
        raise ValueError("source tree must match tracked registry content")
    return tuple(files), tuple(directories)


def _preflight_target_tree(
    target: Path,
    source_files: tuple[PurePosixPath, ...],
    source_directories: tuple[PurePosixPath, ...],
) -> None:
    _preflight_target_ancestors(target)
    if not target.exists() and not target.is_symlink():
        return
    target_stat = target.lstat()
    if stat.S_ISLNK(target_stat.st_mode):
        raise ValueError(f"target must not be a symlink: {target}")
    if not stat.S_ISDIR(target_stat.st_mode):
        raise ValueError(f"target type conflict: expected directory at {target}")

    expected_files = set(source_files)
    expected_directories = set(source_directories)
    for path in sorted(target.rglob("*")):
        relative = PurePosixPath(path.relative_to(target).as_posix())
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            raise ValueError(f"target must not contain a symlink: {relative}")
        if stat.S_ISDIR(path_stat.st_mode):
            if relative in expected_files:
                raise ValueError(f"target type conflict: expected file at {path}")
        elif stat.S_ISREG(path_stat.st_mode):
            if relative in expected_directories:
                raise ValueError(f"target type conflict: expected directory at {path}")
        else:
            raise ValueError(f"target contains an unsupported file type: {relative}")


def _preflight_target_ancestors(target: Path) -> None:
    existing: list[Path] = []
    current = target
    while current != current.parent:
        existing.append(current)
        current = current.parent
    for path in reversed(existing):
        if not path.exists() and not path.is_symlink():
            continue
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            raise ValueError(f"target must not traverse a symlink: {path}")
        if not stat.S_ISDIR(path_stat.st_mode):
            raise ValueError(f"target type conflict: expected directory at {path}")


def _synchronize_tree(
    source: Path,
    target: Path,
    *,
    relative_target: PurePosixPath,
    source_files: tuple[PurePosixPath, ...],
    source_directories: tuple[PurePosixPath, ...],
    metadata: tuple[int, int, int, int],
) -> ConfigTreeResult:
    file_mode, directory_mode, uid, gid = metadata
    for relative in source_directories:
        _ensure_directory(target / Path(*relative.parts), directory_mode, uid, gid)

    changed: list[str] = []
    for relative in source_files:
        destination = target / Path(*relative.parts)
        if _write_file_atomically(source / Path(*relative.parts), destination, file_mode, uid, gid):
            changed.append(_reported_path(relative_target, relative))

    source_file_set = set(source_files)
    for path in sorted(target.rglob("*"), reverse=True):
        relative = PurePosixPath(path.relative_to(target).as_posix())
        if path.is_file() and relative not in source_file_set:
            path.unlink()
            changed.append(_reported_path(relative_target, relative))
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return ConfigTreeResult(changed_paths=tuple(sorted(changed)))


def _ensure_directory(path: Path, mode: int, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    os.chown(path, uid, gid)


def _write_file_atomically(source: Path, target: Path, mode: int, uid: int, gid: int) -> bool:
    content = source.read_bytes()
    unchanged = target.is_file() and target.read_bytes() == content
    if unchanged and _stat_matches(target, mode, uid, gid):
        return False
    temporary = target.with_name(f".{target.name}.infralink-tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def _stat_matches(path: Path, mode: int, uid: int, gid: int) -> bool:
    path_stat = path.stat()
    return (path_stat.st_mode & 0o777, path_stat.st_uid, path_stat.st_gid) == (mode, uid, gid)


def _reported_path(relative_target: PurePosixPath, relative: PurePosixPath) -> str:
    return (PurePosixPath(".") / relative_target / relative).as_posix().lstrip("./")
