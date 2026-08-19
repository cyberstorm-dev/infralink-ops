"""Direct projection of typed observation declarations from a registry checkout."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from infralink.observation import (
    ProjectResult,
    V2MetricProjectResult,
    project,
    project_v2_metric_contracts,
)


def project_registry_observation(
    registry_root: Path,
    *,
    observation_directory: str,
    expected_revision: str,
    as_of: datetime,
) -> ProjectResult:
    """Project one explicit typed directory from a verified registry checkout."""

    root = _verified_registry_root(registry_root, expected_revision)
    source = _revision_bound_directory(
        root, observation_directory, expected_revision, "observation"
    )
    actual_revision = _checkout_revision(root)
    return project([source], registry_revision=actual_revision, as_of=as_of)


def project_registry_v2_metrics(
    registry_root: Path,
    *,
    expected_revision: str,
    catalog_directory: str = "service-catalog/v2",
) -> V2MetricProjectResult:
    """Project V2 component metrics from one verified registry checkout.

    The returned contracts are adapter input only. This function does not read
    prior rendered artifacts or write any runtime state.
    """

    root = _verified_registry_root(registry_root, expected_revision)
    source = _revision_bound_directory(
        root, catalog_directory, expected_revision, "V2 service catalog"
    )
    return project_v2_metric_contracts([source])


def _verified_registry_root(registry_root: Path, expected_revision: str) -> Path:
    root = registry_root.resolve()
    if not root.is_dir():
        raise ValueError(f"registry root must be a directory: {registry_root}")
    if _git_toplevel(root) != root:
        raise ValueError(f"registry root must be the Git checkout top-level: {registry_root}")
    actual_revision = _checkout_revision(root)
    if actual_revision != expected_revision:
        raise ValueError(
            "registry revision mismatch: "
            f"expected {expected_revision}, checkout has {actual_revision}"
        )
    return root


def _revision_bound_directory(
    root: Path,
    relative_path: str,
    expected_revision: str,
    description: str,
) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{description} directory must exist below registry root: {relative_path}")
    source = root / relative
    if not source.is_dir():
        raise ValueError(f"{description} directory must exist below registry root: {relative_path}")
    _ensure_directory_is_in_revision(root, relative, expected_revision)
    _ensure_directory_has_no_symlinks(source)
    _ensure_directory_matches_revision(root, relative, expected_revision)
    return source


def _ensure_directory_is_in_revision(root: Path, relative: Path, expected_revision: str) -> None:
    completed = _run_git(
        root,
        ["cat-file", "-e", f"{expected_revision}:{relative.as_posix()}"],
    )
    if completed.returncode != 0:
        raise ValueError(
            f"source directory is absent from asserted registry revision: {relative.as_posix()}"
        )


def _ensure_directory_has_no_symlinks(source: Path) -> None:
    for path in (source, *source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"source directory contains symlink: {path}")


def _ensure_directory_matches_revision(root: Path, relative: Path, expected_revision: str) -> None:
    pathspec = relative.as_posix()
    if _run_git(
        root, ["diff", "--quiet", "--no-ext-diff", expected_revision, "--", pathspec]
    ).returncode:
        raise ValueError("source directory differs from asserted registry revision")
    for arguments in (
        ["ls-files", "--others", "--exclude-standard", "-z", "--", pathspec],
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", pathspec],
    ):
        if _run_git(root, arguments).stdout:
            raise ValueError("source directory differs from asserted registry revision")


def _git_toplevel(registry_root: Path) -> Path:
    completed = _run_git(registry_root, ["rev-parse", "--show-toplevel"])
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git top-level: {registry_root}")
    return Path(completed.stdout.strip()).resolve()


def _checkout_revision(registry_root: Path) -> str:
    completed = _run_git(registry_root, ["rev-parse", "HEAD"])
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git HEAD: {registry_root}")
    return completed.stdout.strip()


def _run_git(registry_root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(registry_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
