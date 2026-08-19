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
    source = _registry_directory(root, observation_directory, "observation")
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
    source = _registry_directory(root, catalog_directory, "V2 service catalog")
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


def _registry_directory(root: Path, relative_path: str, description: str) -> Path:
    source = (root / relative_path).resolve()
    if root not in source.parents or not source.is_dir():
        raise ValueError(f"{description} directory must exist below registry root: {relative_path}")
    return source


def _git_toplevel(registry_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(registry_root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git top-level: {registry_root}")
    return Path(completed.stdout.strip()).resolve()


def _checkout_revision(registry_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(registry_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git HEAD: {registry_root}")
    return completed.stdout.strip()
