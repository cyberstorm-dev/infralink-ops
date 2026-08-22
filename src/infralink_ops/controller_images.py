"""Bounded Docker image retention for a selected controller image."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

import yaml

CURRENT_TAG = "infralink-controller-cache:current"
PREVIOUS_TAG = "infralink-controller-cache:previous"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _run(docker: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([docker, *arguments], text=True, capture_output=True, check=check)
    except OSError as error:
        return subprocess.CompletedProcess(
            [docker, *arguments], returncode=125, stdout="", stderr=str(error)
        )


def _error(reason: str, failures: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "error",
        "reason": reason,
        "removed": [],
        "blocked": [],
        "failures": sorted(failures or []),
    }


def _image_id(docker: str, reference: str) -> str | None:
    completed = _run(docker, "image", "inspect", "--format", "{{.Id}}", reference, check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _image_references(docker: str, identifier: str, field: str) -> list[str]:
    completed = _run(
        docker,
        "image",
        "inspect",
        "--format",
        f"{{{{json .{field}}}}}",
        identifier,
        check=False,
    )
    if completed.returncode != 0:
        return []
    try:
        values = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def _has_container_reference(docker: str, identifier: str) -> bool:
    completed = _run(
        docker,
        "ps",
        "--all",
        "--filter",
        f"ancestor={identifier}",
        "--quiet",
        check=False,
    )
    # An unavailable Docker daemon is not evidence that an image is safe to delete.
    return completed.returncode != 0 or bool(completed.stdout.strip())


def retain_and_prune(docker: str, repository: str, current: str) -> tuple[dict[str, Any], int]:
    """Keep the selected image and one predecessor; prune only unreferenced stale images.

    The caller supplies the desired immutable reference. Cache tags are evidence for
    rollback only and are never used to select configuration.
    """

    expected_current = f"{repository}@sha256:"
    digest = current.removeprefix(expected_current)
    if not current.startswith(expected_current) or _SHA256.fullmatch(digest) is None:
        return _error("selected_controller_image_invalid"), 64

    current_id = _image_id(docker, current)
    if current_id is None:
        return _error("selected_controller_image_unavailable"), 78

    failures: list[str] = []
    removed: list[str] = []
    blocked: list[str] = []
    prior_current_id = _image_id(docker, CURRENT_TAG)
    if prior_current_id is not None and prior_current_id != current_id:
        if _run(docker, "image", "rm", PREVIOUS_TAG, check=False).returncode != 0:
            failures.append(PREVIOUS_TAG)
        elif _run(docker, "tag", prior_current_id, PREVIOUS_TAG, check=False).returncode != 0:
            failures.append(f"tag:{PREVIOUS_TAG}")
    if failures:
        return _error("controller_cache_rotation_failed", failures), 75
    if _run(docker, "tag", current_id, CURRENT_TAG, check=False).returncode != 0:
        failures.append(f"tag:{CURRENT_TAG}")
        return _error("controller_cache_rotation_failed", failures), 75

    retained = {current_id}
    previous_id = _image_id(docker, PREVIOUS_TAG)
    if previous_id is not None:
        retained.add(previous_id)

    prefix = f"{repository}@sha256:"
    listed = _run(docker, "image", "ls", "--all", "--quiet", "--no-trunc", check=False)
    if listed.returncode != 0:
        return _error("controller_image_listing_failed"), 75
    for identifier in {value for value in listed.stdout.splitlines() if value}:
        if identifier in retained:
            continue
        digests = _image_references(docker, identifier, "RepoDigests")
        if not any(digest.startswith(prefix) for digest in digests):
            continue
        if any(not digest.startswith(prefix) for digest in digests):
            blocked.append(identifier)
            continue
        tags = _image_references(docker, identifier, "RepoTags")
        if any(not tag.startswith(f"{repository}:") for tag in tags):
            blocked.append(identifier)
            continue
        if _has_container_reference(docker, identifier):
            blocked.append(identifier)
            continue
        for tag in tags:
            if tag not in {CURRENT_TAG, PREVIOUS_TAG}:
                if _run(docker, "image", "rm", tag, check=False).returncode != 0:
                    failures.append(tag)
        if _run(docker, "image", "rm", identifier, check=False).returncode == 0:
            removed.append(identifier)
        else:
            failures.append(identifier)
    return {
        "status": "warning" if failures else "ok",
        "current": current_id,
        "previous": previous_id,
        "removed": sorted(removed),
        "blocked": sorted(blocked),
        "failures": sorted(failures),
    }, 0


def main(argv: list[str] | None = None) -> int:
    """Run controller-image cache maintenance with an agent-readable envelope."""

    parser = argparse.ArgumentParser(prog="infralink-controller-images")
    commands = parser.add_subparsers(dest="command", required=True)
    retain = commands.add_parser("retain-and-prune")
    retain.add_argument("--docker", default="docker")
    retain.add_argument("--repository", required=True)
    retain.add_argument("--current", required=True)
    args = parser.parse_args(argv)

    result, exit_code = retain_and_prune(args.docker, args.repository, args.current)
    payload = {
        "schema_version": "infralink.ops.controller-image-cache/v1",
        "ok": exit_code == 0,
        "command": {
            "path": [args.command],
            "args": {"repository": args.repository, "current": args.current},
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
