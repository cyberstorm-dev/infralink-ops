from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from infralink_ops.registry_checkout import RegistryCheckoutError, fetch_configured_registry


def _git(*argv: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *argv], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "test@example.invalid", cwd=origin)
    _git("config", "user.name", "Test", cwd=origin)
    (origin / "registry.yml").write_text("first\n", encoding="utf-8")
    _git("add", "registry.yml", cwd=origin)
    _git("commit", "-qm", "first", cwd=origin)

    registry = tmp_path / "registry"
    subprocess.run(["git", "clone", "-q", str(origin), str(registry)], check=True)
    identity = tmp_path / "registry-read"
    known_hosts = tmp_path / "registry-known_hosts"
    identity.write_text("not-used-for-local-git\n", encoding="utf-8")
    known_hosts.write_text("not-used-for-local-git\n", encoding="utf-8")
    return origin, registry, identity, known_hosts


def test_fetches_declared_ref_and_returns_exact_detached_revision(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    (origin / "registry.yml").write_text("second\n", encoding="utf-8")
    _git("add", "registry.yml", cwd=origin)
    _git("commit", "-qm", "second", cwd=origin)
    expected = _git("rev-parse", "HEAD", cwd=origin)

    result = fetch_configured_registry(
        registry,
        configured_remote=str(origin),
        configured_ref="main",
        identity_file=identity,
        known_hosts_file=known_hosts,
    )

    assert result.revision == expected
    assert _git("rev-parse", "HEAD", cwd=registry) == expected
    detached = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"], cwd=registry, capture_output=True, text=True
    )
    assert detached.returncode == 1


def test_rejects_dirty_checkout_before_fetch(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    before = _git("rev-parse", "HEAD", cwd=registry)
    (registry / "local-state.yml").write_text("must-not-be-preserved\n", encoding="utf-8")

    with pytest.raises(RegistryCheckoutError, match="local changes"):
        fetch_configured_registry(
            registry,
            configured_remote=str(origin),
            configured_ref="main",
            identity_file=identity,
            known_hosts_file=known_hosts,
        )

    assert _git("rev-parse", "HEAD", cwd=registry) == before


def test_rejects_origin_that_differs_from_declared_remote(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)

    with pytest.raises(RegistryCheckoutError, match="does not match"):
        fetch_configured_registry(
            registry,
            configured_remote=str(tmp_path / "another-origin"),
            configured_ref="main",
            identity_file=identity,
            known_hosts_file=known_hosts,
        )

    assert origin.is_dir()


def test_rejects_missing_explicit_trust_file(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)

    with pytest.raises(RegistryCheckoutError, match="trust file is missing"):
        fetch_configured_registry(
            registry,
            configured_remote=str(origin),
            configured_ref="main",
            identity_file=identity,
            known_hosts_file=known_hosts.with_name("missing-known_hosts"),
        )
