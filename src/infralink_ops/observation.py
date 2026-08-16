"""Direct projection of typed observation declarations from a registry checkout."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from infralink.observation import ProjectResult, project


def project_registry_observation(
    registry_root: Path,
    *,
    observation_directory: str,
    expected_revision: str,
    as_of: datetime,
) -> ProjectResult:
    """Project one explicit typed directory from a verified registry checkout."""

    root = registry_root.resolve()
    if not root.is_dir():
        raise ValueError(f"registry root must be a directory: {registry_root}")
    source = (root / observation_directory).resolve()
    if root not in source.parents or not source.is_dir():
        raise ValueError(
            f"observation directory must exist below registry root: {observation_directory}"
        )
    actual_revision = _checkout_revision(root)
    if actual_revision != expected_revision:
        raise ValueError(
            "registry revision mismatch: "
            f"expected {expected_revision}, checkout has {actual_revision}"
        )
    return project([source], registry_revision=actual_revision, as_of=as_of)


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
