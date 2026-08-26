from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from infralink_ops.artifact_renderer_source import (
    ArtifactRendererSourceError,
    load_artifact_renderer_source,
    verify_artifact_renderer_checkout,
)


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *arguments], text=True).strip()


def _checkout(tmp_path: Path, repository: str) -> tuple[Path, str]:
    checkout = tmp_path / "renderer"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    _git(checkout, "config", "user.email", "test@example.invalid")
    _git(checkout, "config", "user.name", "Test")
    (checkout / "renderer.py").write_text("print('renderer')\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "initial")
    _git(checkout, "remote", "add", "origin", repository)
    return checkout, _git(checkout, "rev-parse", "HEAD")


def _lock(tmp_path: Path, repository: str, revision: str) -> Path:
    lock = tmp_path / "renderer.lock.yml"
    lock.write_text(
        "schema_version: infralink.ops.artifact-renderer-source/v1\n"
        f"repository: {repository}\n"
        f"revision: '{revision}'\n",
        encoding="ascii",
    )
    return lock


def test_loads_and_verifies_an_exact_clean_renderer_checkout(tmp_path: Path) -> None:
    repository = "https://github.com/example/renderer.git"
    checkout, revision = _checkout(tmp_path, repository)
    lock = _lock(tmp_path, repository, revision)

    source = load_artifact_renderer_source(lock)

    assert source.repository == repository
    assert source.revision == revision
    assert source.lock_digest == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert verify_artifact_renderer_checkout(source, checkout) == checkout.resolve()


def test_rejects_invalid_renderer_lock_declarations(tmp_path: Path) -> None:
    lock = tmp_path / "renderer.lock.yml"
    lock.write_text("repository: https://github.com/example/renderer.git\n", encoding="ascii")

    with pytest.raises(ArtifactRendererSourceError, match="declaration"):
        load_artifact_renderer_source(lock)


def test_rejects_a_checkout_with_a_different_revision(tmp_path: Path) -> None:
    repository = "https://github.com/example/renderer.git"
    checkout, _ = _checkout(tmp_path, repository)
    source = load_artifact_renderer_source(_lock(tmp_path, repository, "0" * 40))

    with pytest.raises(ArtifactRendererSourceError, match="revision"):
        verify_artifact_renderer_checkout(source, checkout)


def test_rejects_a_checkout_with_a_different_origin(tmp_path: Path) -> None:
    checkout, revision = _checkout(tmp_path, "https://github.com/example/other.git")
    source = load_artifact_renderer_source(
        _lock(tmp_path, "https://github.com/example/renderer.git", revision)
    )

    with pytest.raises(ArtifactRendererSourceError, match="repository"):
        verify_artifact_renderer_checkout(source, checkout)


def test_rejects_a_dirty_renderer_checkout(tmp_path: Path) -> None:
    repository = "https://github.com/example/renderer.git"
    checkout, revision = _checkout(tmp_path, repository)
    (checkout / "renderer.py").write_text("print('changed')\n", encoding="utf-8")
    source = load_artifact_renderer_source(_lock(tmp_path, repository, revision))

    with pytest.raises(ArtifactRendererSourceError, match="clean"):
        verify_artifact_renderer_checkout(source, checkout)
