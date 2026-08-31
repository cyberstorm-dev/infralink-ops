from __future__ import annotations

from pathlib import Path

import tomllib

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


def test_prune_unused_images_is_bounded_when_docker_never_returns(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    docker.chmod(0o755)

    result, exit_code = prune_unused_images(str(docker), timeout_seconds=0.01)

    assert exit_code == 0
    assert result == {"status": "warning", "reason": "docker_image_prune_timed_out"}


def test_image_cleanup_is_private_to_controller_reconcile() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert "infralink-controller-images" not in project["project"]["scripts"]


def test_runtime_release_version_is_semver() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    major, minor, patch = project["project"]["version"].split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
