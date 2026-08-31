"""Bounded Docker image cleanup for successful controller reconciliations."""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any

import yaml


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


def main(argv: list[str] | None = None) -> int:
    """Run Docker image cleanup with an agent-readable envelope."""

    parser = argparse.ArgumentParser(prog="infralink-controller-images")
    commands = parser.add_subparsers(dest="command", required=True)
    prune = commands.add_parser("prune-unused")
    prune.add_argument("--docker", default="docker")
    args = parser.parse_args(argv)

    result, exit_code = prune_unused_images(args.docker)
    payload = {
        "schema_version": "infralink.ops.docker-image-cleanup/v1",
        "ok": exit_code == 0,
        "command": {
            "path": [args.command],
            "args": {},
            "flags": ["--docker"] if args.docker != "docker" else [],
        },
        "result": result,
        "next_actions": [],
        "meta": {"truncated": False},
    }
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
