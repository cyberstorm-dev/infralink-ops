"""Bounded Docker image cleanup for successful controller reconciliations."""

from __future__ import annotations

import subprocess
from typing import Any


def _run(
    docker: str, *arguments: str, timeout_seconds: float
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [docker, *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as error:
        return subprocess.CompletedProcess(
            [docker, *arguments], returncode=125, stdout="", stderr=str(error)
        )


def prune_unused_images(
    docker: str, *, timeout_seconds: float = 60.0
) -> tuple[dict[str, Any], int]:
    """Remove only Docker images unused by every container.

    Docker owns the reachability check. This command deliberately never prunes
    containers, volumes, networks, logs, or Registry state.
    """

    completed = _run(docker, "image", "prune", "--all", "--force", timeout_seconds=timeout_seconds)
    if completed is None:
        return {"status": "warning", "reason": "docker_image_prune_timed_out"}, 0
    if completed.returncode != 0:
        return {"status": "warning", "reason": "docker_image_prune_failed"}, 0
    return {"status": "ok"}, 0
