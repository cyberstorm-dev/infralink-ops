from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import yaml

from infralink_ops.controller_artifacts import main

HOST_ID = "11111111-1111-1111-1111-111111111111"


def _commit(registry: Path) -> str:
    subprocess.run(["git", "init", "-q", str(registry)], check=True)
    subprocess.run(
        ["git", "-C", str(registry), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(registry), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(registry), "add", "."], check=True)
    subprocess.run(["git", "-C", str(registry), "commit", "-qm", "initial"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(registry), "rev-parse", "HEAD"], text=True
    ).strip()


def _registry(tmp_path: Path) -> tuple[Path, str]:
    registry = tmp_path / "registry"
    source = registry / "operations" / "rendered" / "config.yml"
    source.parent.mkdir(parents=True)
    body = b"value: declared\n"
    source.write_bytes(body)
    deployment = {
        "generated_artifacts": [
            {
                "id": "declared-config",
                "provider": "artifact-sync",
                "source": {
                    "path": "operations/rendered/config.yml",
                    "sha256": hashlib.sha256(body).hexdigest(),
                },
                "target": {
                    "path": "/opt/services/config/example/config.yml",
                    "mode": "0644",
                    "owner_uid": 0,
                    "owner_gid": 0,
                },
            },
            {"id": "private-projection", "provider": "gatus-core-config"},
        ]
    }
    path = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(deployment), encoding="utf-8")
    return registry, _commit(registry)


def test_apply_materializes_only_generic_declared_artifacts(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            HOST_ID,
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 0
    assert payload["ok"] is True
    assert payload["result"] == {"changed_config_paths": ["example/config.yml"]}
    assert (
        tmp_path / "services" / "config" / "example" / "config.yml"
    ).read_text() == "value: declared\n"


def test_apply_rejects_a_different_registry_revision(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            "0" * 40,
            "--uuid",
            HOST_ID,
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 78
    assert payload["error"] == {"code": "generated_artifact_materialization_failed"}


def test_apply_rejects_host_path_escape_without_writing(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            "../../outside",
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 78
    assert payload["error"] == {"code": "generated_artifact_materialization_failed"}
    assert not (tmp_path / "services").exists()


def test_apply_rejects_noncanonical_host_id_without_writing(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid=------------------------------------",
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 78
    assert payload["error"] == {"code": "generated_artifact_materialization_failed"}
    assert not (tmp_path / "services").exists()


def test_apply_preflights_later_invalid_artifact_before_writing(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    deployment_path = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
    deployment["generated_artifacts"].append(
        {
            "id": "invalid-later-artifact",
            "provider": "artifact-sync",
            "source": {"path": "operations/rendered/config.yml", "sha256": "0" * 64},
            "target": {
                "path": "/opt/services/config/example/late.yml",
                "mode": "0644",
                "owner_uid": 0,
                "owner_gid": 0,
            },
        }
    )
    deployment_path.write_text(yaml.safe_dump(deployment), encoding="utf-8")
    revision = _commit(registry)
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            HOST_ID,
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 78
    assert payload["error"] == {"code": "generated_artifact_materialization_failed"}
    assert not (tmp_path / "services").exists()


def test_apply_preflights_later_invalid_destination_before_writing(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    deployment_path = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
    body = b"later: declared\n"
    source = registry / "operations" / "rendered" / "later.yml"
    source.write_bytes(body)
    deployment["generated_artifacts"].append(
        {
            "id": "later-artifact",
            "provider": "host-config",
            "source": {
                "path": "operations/rendered/later.yml",
                "sha256": hashlib.sha256(body).hexdigest(),
            },
            "target": {
                "path": "/opt/services/config/example/later.yml",
                "mode": "0644",
                "owner_uid": 0,
                "owner_gid": 0,
            },
        }
    )
    deployment_path.write_text(yaml.safe_dump(deployment), encoding="utf-8")
    revision = _commit(registry)
    target = tmp_path / "services" / "config" / "example" / "later.yml"
    target.parent.mkdir(parents=True)
    target.symlink_to(tmp_path / "outside")
    payload, status = main(
        [
            "apply",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            HOST_ID,
            "--services-dir",
            str(tmp_path / "services"),
        ]
    )
    assert status == 78
    assert payload["error"] == {"code": "generated_artifact_materialization_failed"}
    assert not (tmp_path / "services" / "config" / "example" / "config.yml").exists()
