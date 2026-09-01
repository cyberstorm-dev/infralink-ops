import subprocess
from pathlib import Path

import tomllib
import yaml

from infralink_ops.controller_protected_transitions import validate

HOST_ID = "11111111-1111-4111-8111-111111111111"


def _registry(tmp_path: Path) -> tuple[Path, str, Path]:
    registry = tmp_path / "registry"
    deployment = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(
        yaml.safe_dump(
            {
                "services": {"protected": ["api"]},
                "protected_image_transitions": [],
            }
        ),
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  api:\n    image: ghcr.io/example/api@sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", registry], check=True)
    subprocess.run(
        ["git", "-C", registry, "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", registry, "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", registry, "add", "."], check=True)
    subprocess.run(["git", "-C", registry, "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", registry, "rev-parse", "HEAD"], text=True
    ).strip()
    return registry, revision, compose


def test_rejects_an_unapproved_protected_image_change(tmp_path: Path) -> None:
    registry, revision, compose = _registry(tmp_path)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'compose -f'*'ps -q --all api') echo container-id ;;\n"
        "  *'inspect --format {{.Config.Image}} container-id') "
        "echo ghcr.io/example/api@sha256:" + "a" * 64 + " ;;\n"
        "  *'inspect --format {{.Image}} container-id') echo image-id ;;\n"
        "  *'image inspect --format {{range .RepoDigests}}{{println .}}{{end}} image-id') "
        "echo ghcr.io/example/api@sha256:" + "a" * 64 + " ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result, status = validate(
        registry=registry,
        registry_revision=revision,
        host_id=HOST_ID,
        compose=compose,
        docker=str(docker),
    )

    assert status == 78
    assert result["error"] == "protected_transition_unauthorized"


def test_reports_equal_resolved_digest_as_representation_equivalent(tmp_path: Path) -> None:
    registry, revision, compose = _registry(tmp_path)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'compose -f'*'ps -q --all api') echo container-id ;;\n"
        "  *'inspect --format {{.Config.Image}} container-id') "
        "echo ghcr.io/example/api:stable ;;\n"
        "  *'inspect --format {{.Image}} container-id') echo image-id ;;\n"
        "  *'image inspect --format {{range .RepoDigests}}{{println .}}{{end}} image-id') "
        "echo ghcr.io/example/api@sha256:" + "b" * 64 + " ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result, status = validate(
        registry=registry,
        registry_revision=revision,
        host_id=HOST_ID,
        compose=compose,
        docker=str(docker),
    )

    assert status == 0
    assert result["transitions"] == []
    assert result["representation_equivalent"] == [
        {
            "service": "api",
            "configured": {
                "live": "ghcr.io/example/api:stable",
                "desired": "ghcr.io/example/api@sha256:" + "b" * 64,
            },
            "resolved": "ghcr.io/example/api@sha256:" + "b" * 64,
        }
    ]


def test_rejects_matching_digest_from_a_different_repository(tmp_path: Path) -> None:
    registry, revision, compose = _registry(tmp_path)
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'compose -f'*'ps -q --all api') echo container-id ;;\n"
        "  *'inspect --format {{.Config.Image}} container-id') "
        "echo ghcr.io/other/api:stable ;;\n"
        "  *'inspect --format {{.Image}} container-id') echo image-id ;;\n"
        "  *'image inspect --format {{range .RepoDigests}}{{println .}}{{end}} image-id') "
        "echo ghcr.io/other/api@sha256:" + "b" * 64 + " ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result, status = validate(
        registry=registry,
        registry_revision=revision,
        host_id=HOST_ID,
        compose=compose,
        docker=str(docker),
    )

    assert status == 78
    assert result["error"] == "protected_transition_unauthorized"


def test_protected_transition_validator_has_no_console_script() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert "infralink-controller-protected-transitions" not in project["project"].get("scripts", {})
