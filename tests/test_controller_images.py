from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points

import yaml

from infralink_ops.controller_images import retain_and_prune


def test_retain_and_prune_reports_missing_selected_image(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    result, exit_code = retain_and_prune(
        str(docker),
        "ghcr.io/example/controller",
        "ghcr.io/example/controller@sha256:" + ("a" * 64),
    )

    assert exit_code == 78
    assert result == {
        "status": "error",
        "reason": "selected_controller_image_unavailable",
        "removed": [],
        "blocked": [],
        "failures": [],
    }


def test_image_cache_cli_returns_hateoas_yaml(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_images",
            "retain-and-prune",
            "--docker",
            str(docker),
            "--repository",
            "ghcr.io/example/controller",
            "--current",
            "ghcr.io/example/controller@sha256:" + ("a" * 64),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 78
    payload = yaml.safe_load(completed.stdout)
    assert payload["schema_version"] == "infralink.ops.controller-image-cache/v1"
    assert payload["ok"] is False
    assert payload["command"]["path"] == ["retain-and-prune"]
    assert payload["result"]["reason"] == "selected_controller_image_unavailable"


def test_installs_controller_image_cache_runnable() -> None:
    scripts = entry_points(group="console_scripts")
    command = next(entry for entry in scripts if entry.name == "infralink-controller-images")
    assert command.value == "infralink_ops.controller_images:main"
