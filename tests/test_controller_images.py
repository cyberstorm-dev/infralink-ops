from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import tomllib
import yaml

from infralink_ops.controller_images import retain_and_prune

CURRENT_REFERENCE = "ghcr.io/example/controller@sha256:" + ("a" * 64)


def test_retain_and_prune_reports_missing_selected_image(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    result, exit_code = retain_and_prune(
        str(docker),
        "ghcr.io/example/controller",
        CURRENT_REFERENCE,
    )

    assert exit_code == 78
    assert result == {
        "status": "error",
        "reason": "selected_controller_image_unavailable",
        "removed": [],
        "blocked": [],
        "failures": [],
    }


def test_retain_and_prune_rejects_a_non_digest_or_foreign_current_reference(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    docker.chmod(0o755)

    result, exit_code = retain_and_prune(
        str(docker), "ghcr.io/example/controller", "ghcr.io/example/other:main"
    )

    assert exit_code == 64
    assert result["reason"] == "selected_controller_image_invalid"


def test_retain_and_prune_does_not_prune_after_cache_rotation_failure(
    tmp_path, monkeypatch
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        f"  'image inspect --format {{{{.Id}}}} {CURRENT_REFERENCE}') echo sha256:current ;;\n"
        "  'image inspect --format {{.Id}} "
        "infralink-controller-cache:current') echo sha256:prior ;;\n"
        "  'image rm infralink-controller-cache:previous') exit 1 ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("DOCKER_LOG", str(log))

    result, exit_code = retain_and_prune(
        str(docker),
        "ghcr.io/example/controller",
        CURRENT_REFERENCE,
    )

    assert exit_code == 75
    assert result["reason"] == "controller_cache_rotation_failed"
    assert "image ls --all --quiet --no-trunc" not in log.read_text().splitlines()


def test_retain_and_prune_reports_image_list_failure_without_pruning(tmp_path, monkeypatch) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        f"  'image inspect --format {{{{.Id}}}} {CURRENT_REFERENCE}') echo sha256:current ;;\n"
        "  'image inspect --format {{.Id}} infralink-controller-cache:current') exit 1 ;;\n"
        "  'tag sha256:current infralink-controller-cache:current') : ;;\n"
        "  'image inspect --format {{.Id}} infralink-controller-cache:previous') exit 1 ;;\n"
        "  'image ls --all --quiet --no-trunc') exit 1 ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("DOCKER_LOG", str(log))

    result, exit_code = retain_and_prune(
        str(docker),
        "ghcr.io/example/controller",
        CURRENT_REFERENCE,
    )

    assert exit_code == 75
    assert result["reason"] == "controller_image_listing_failed"
    assert not any(line.startswith("image rm sha256:") for line in log.read_text().splitlines())


def test_retain_and_prune_removes_only_unreferenced_repository_images(
    tmp_path, monkeypatch
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        f"  'image inspect --format {{{{.Id}}}} {CURRENT_REFERENCE}') echo sha256:current ;;\n"
        "  'image inspect --format {{.Id}} infralink-controller-cache:current') exit 1 ;;\n"
        "  'tag sha256:current infralink-controller-cache:current') : ;;\n"
        "  'image inspect --format {{.Id}} infralink-controller-cache:previous') exit 1 ;;\n"
        "  'image ls --all --quiet --no-trunc') printf '%s\\n' "
        "sha256:current sha256:stale sha256:shared ;;\n"
        "  'image inspect --format {{json .RepoDigests}} sha256:current') "
        "echo '[\"ghcr.io/example/controller@sha256:current\"]' ;;\n"
        "  'image inspect --format {{json .RepoDigests}} sha256:stale') "
        "echo '[\"ghcr.io/example/controller@sha256:stale\"]' ;;\n"
        "  'image inspect --format {{json .RepoDigests}} sha256:shared') "
        'echo \'["ghcr.io/example/controller@sha256:shared",'
        '"example/other@sha256:shared"]\' ;;\n'
        "  'ps --all --filter ancestor=sha256:stale --quiet') : ;;\n"
        "  'ps --all --filter ancestor=sha256:shared --quiet') : ;;\n"
        "  'image rm ghcr.io/example/controller:retired') : ;;\n"
        "  'image rm sha256:stale') : ;;\n"
        "  'image rm sha256:shared') : ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("DOCKER_LOG", str(log))

    result, exit_code = retain_and_prune(
        str(docker),
        "ghcr.io/example/controller",
        CURRENT_REFERENCE,
    )

    assert exit_code == 0
    assert result["removed"] == ["sha256:stale"]
    assert result["blocked"] == ["sha256:shared"]
    calls = log.read_text().splitlines()
    assert "image rm sha256:stale" in calls
    assert "image rm sha256:shared" not in calls


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
            CURRENT_REFERENCE,
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


def test_bumps_minor_runtime_release_version() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert project["project"]["version"] == "0.2.5"
