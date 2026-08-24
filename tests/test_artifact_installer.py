import hashlib
import os
from pathlib import Path

import pytest

from infralink_ops.artifact_installer import (
    ArtifactInstallError,
    install_declared_artifact,
    read_declared_artifact,
    resolve_declared_artifact_target,
)


def _source(path: str, body: bytes) -> dict[str, object]:
    return {"source": {"path": path, "sha256": hashlib.sha256(body).hexdigest()}}


def _target(path: str) -> dict[str, object]:
    return {
        "target": {
            "path": path,
            "mode": "0640",
            "owner_uid": os.getuid(),
            "owner_gid": os.getgid(),
        }
    }


def test_reads_verifies_and_installs_declared_artifact(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    source_path = registry / "rendered" / "config.yml"
    source_path.parent.mkdir(parents=True)
    body = b"version: 1\n"
    source_path.write_bytes(body)
    services = tmp_path / "services"

    artifact = read_declared_artifact(registry, _source("rendered/config.yml", body))
    target = resolve_declared_artifact_target(
        services, _target("/opt/services/config/example/config.yml")
    )
    assert install_declared_artifact(artifact.body, target, config_root=services / "config")

    assert target.destination.read_bytes() == body
    assert target.destination.stat().st_mode & 0o777 == 0o640
    assert not install_declared_artifact(artifact.body, target, config_root=services / "config")


def test_rejects_digest_mismatch_and_target_escape_before_writing(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    source_path = registry / "artifact.yml"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("actual\n", encoding="utf-8")
    services = tmp_path / "services"

    with pytest.raises(ArtifactInstallError, match="source digest mismatch"):
        read_declared_artifact(registry, _source("artifact.yml", b"expected\n"))
    with pytest.raises(ArtifactInstallError, match="target escapes services directory"):
        resolve_declared_artifact_target(services, _target("/opt/services/../outside"))
    assert not services.exists()


def test_repairs_only_empty_declared_config_destination(tmp_path: Path) -> None:
    services = tmp_path / "services"
    target = resolve_declared_artifact_target(
        services, _target("/opt/services/config/example/config.yml")
    )
    target.destination.mkdir(parents=True)

    assert install_declared_artifact(b"ok\n", target, config_root=services / "config")
    assert target.destination.read_bytes() == b"ok\n"
