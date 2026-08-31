from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import tomllib
import yaml

from infralink_ops.controller_images import prune_unused_images


def test_prune_unused_images_removes_only_images_not_used_by_containers(
    tmp_path: Path, monkeypatch
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        "[ \"$*\" = 'image prune --all --force' ]\n"
        "printf '%s\\n' 'Deleted Images:' 'Total reclaimed space: 26GB'\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("DOCKER_LOG", str(log))

    result, exit_code = prune_unused_images(str(docker))

    assert exit_code == 0
    assert result == {"status": "ok"}
    assert log.read_text(encoding="utf-8").splitlines() == ["image prune --all --force"]


def test_prune_unused_images_reports_a_warning_without_failing_reconcile(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    result, exit_code = prune_unused_images(str(docker))

    assert exit_code == 0
    assert result == {"status": "warning", "reason": "docker_image_prune_failed"}


def test_image_cleanup_cli_returns_hateoas_yaml(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_images",
            "prune-unused",
            "--docker",
            str(docker),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = yaml.safe_load(completed.stdout)
    assert payload["schema_version"] == "infralink.ops.docker-image-cleanup/v1"
    assert payload["ok"] is True
    assert payload["command"]["path"] == ["prune-unused"]
    assert payload["result"] == {"status": "warning", "reason": "docker_image_prune_failed"}


def test_installs_docker_image_cleanup_runnable() -> None:
    scripts = entry_points(group="console_scripts")
    command = next(entry for entry in scripts if entry.name == "infralink-controller-images")
    assert command.value == "infralink_ops.controller_images:main"


def test_runtime_release_version_is_semver() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    major, minor, patch = project["project"]["version"].split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
