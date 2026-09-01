from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

UUID = "11111111-1111-1111-1111-111111111111"


def commit_registry(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "registry"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_resolve_host_images_canonicalizes_and_locks_tag_selectors(
    tmp_path: Path, monkeypatch
) -> None:
    from infralink_ops.image_resolution import resolve_host_image_evidence, resolve_host_images

    deployment = tmp_path / "registry" / "hosts" / UUID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(
        """images:
  elasticsearch:
    repository: docker.example/elasticsearch
    tag: "7.10.2"
  nginx:
    repository: nginx
    sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""",
        encoding="utf-8",
    )
    revision = commit_registry(tmp_path / "registry")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    elasticsearch_digest = "a" * 64
    nginx_digest = "b" * 64
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == pull ]]; then exit 0; fi\n'
        "if [[ \"$1 $2\" == 'image inspect' ]]; then\n"
        '  if [[ "$*" == *elasticsearch* ]]; then\n'
        f"    printf '%s\\n' 'docker.example/elasticsearch@sha256:{elasticsearch_digest}'\n"
        "  else\n"
        f"    printf '%s\\n' 'nginx@sha256:{nginx_digest}'\n"
        "  fi\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    assert resolve_host_images(tmp_path / "registry", UUID, expected_revision=revision) == {
        "elasticsearch": "docker.example/elasticsearch@sha256:" + elasticsearch_digest,
        "nginx": "docker.io/library/nginx@sha256:" + nginx_digest,
    }
    assert resolve_host_image_evidence(tmp_path / "registry", UUID, expected_revision=revision)[
        "configured"
    ] == {
        "elasticsearch": "docker.example/elasticsearch:7.10.2",
        "nginx": "nginx@sha256:" + nginx_digest,
    }


def test_resolve_host_images_rejects_dirty_registry_before_docker_use(tmp_path: Path) -> None:
    from infralink_ops.image_resolution import ImageResolutionError, resolve_host_images

    deployment = tmp_path / "registry" / "hosts" / UUID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    deployment.write_text("images: {}\n", encoding="utf-8")
    revision = commit_registry(tmp_path / "registry")
    deployment.write_text("images: {nginx: nginx}\n", encoding="utf-8")

    with pytest.raises(ImageResolutionError, match="registry checkout must be clean"):
        resolve_host_images(tmp_path / "registry", UUID, expected_revision=revision)


def test_resolve_controller_reference_uses_declared_sha_then_head_branch(tmp_path: Path) -> None:
    from infralink_ops.image_resolution import resolve_controller_reference

    deployment = tmp_path / "registry" / "hosts" / UUID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(
        """controller:
  image:
    repository: ghcr.io/example/controller
    sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    tag: ignored
""",
        encoding="utf-8",
    )
    revision = commit_registry(tmp_path / "registry")

    assert resolve_controller_reference(
        tmp_path / "registry", UUID, expected_revision=revision
    ) == ("ghcr.io/example/controller@sha256:" + "a" * 64)

    deployment.write_text(
        """controller:
  image:
    repository: ghcr.io/example/controller
    tag: head
    branch: release
""",
        encoding="utf-8",
    )
    revision = commit_registry(tmp_path / "registry")
    assert (
        resolve_controller_reference(tmp_path / "registry", UUID, expected_revision=revision)
        == "ghcr.io/example/controller:release"
    )


def test_controller_image_resolution_is_private_runtime_code() -> None:
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"

    project_text = project.read_text(encoding="utf-8")
    assert "infralink-controller-image-resolution" not in project_text
    assert "infralink-controller-reference" not in project_text
