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


def test_discards_dirty_runtime_cache_before_converging_to_configured_ref(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    (origin / "registry.yml").write_text("second\n", encoding="utf-8")
    _git("add", "registry.yml", cwd=origin)
    _git("commit", "-qm", "advance", cwd=origin)
    expected = _git("rev-parse", "HEAD", cwd=origin)
    (registry / "registry.yml").write_text("staged local state\n", encoding="utf-8")
    _git("add", "registry.yml", cwd=registry)
    (registry / "local-state.yml").write_text("must-not-be-preserved\n", encoding="utf-8")

    result = fetch_configured_registry(
        registry,
        configured_remote=str(origin),
        configured_ref="main",
        identity_file=identity,
        known_hosts_file=known_hosts,
    )

    assert result.revision == expected
    assert _git("rev-parse", "HEAD", cwd=registry) == expected
    assert (registry / "registry.yml").read_text(encoding="utf-8") == "second\n"
    assert not (registry / "local-state.yml").exists()
    assert _git("status", "--porcelain=v1", "--untracked-files=all", cwd=registry) == ""


def test_discards_retired_initialized_submodule_and_preserves_active_one(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    submodule = tmp_path / "submodule"
    submodule.mkdir()
    _git("init", "-q", "-b", "main", cwd=submodule)
    _git("config", "user.email", "test@example.invalid", cwd=submodule)
    _git("config", "user.name", "Test", cwd=submodule)
    (submodule / "value.txt").write_text("one\n", encoding="utf-8")
    _git("add", "value.txt", cwd=submodule)
    _git("commit", "-qm", "initial", cwd=submodule)
    expected_submodule = _git("rev-parse", "HEAD", cwd=submodule)

    for path in ("retired", "active"):
        _git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(submodule),
            path,
            cwd=origin,
        )
    _git("commit", "-qm", "add modules", cwd=origin)
    _git("-c", "protocol.file.allow=always", "pull", cwd=registry)
    _git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--checkout",
        cwd=registry,
    )

    (submodule / "value.txt").write_text("two\n", encoding="utf-8")
    _git("commit", "-am", "advance", cwd=submodule)
    _git("fetch", cwd=registry / "retired")
    _git("checkout", "origin/main", cwd=registry / "retired")
    _git("rm", "-f", "retired", cwd=origin)
    _git("commit", "-qm", "retire module", cwd=origin)
    expected = _git("rev-parse", "HEAD", cwd=origin)

    result = fetch_configured_registry(
        registry,
        configured_remote=str(origin),
        configured_ref="main",
        identity_file=identity,
        known_hosts_file=known_hosts,
    )

    assert result.revision == expected
    assert not (registry / "retired").exists()
    assert _git("rev-parse", "HEAD", cwd=registry / "active") == expected_submodule
    assert _git("status", "--porcelain=v1", "--untracked-files=all", cwd=registry) == ""


def test_parent_checkout_ignores_declared_submodule_worktree_state(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    submodule = tmp_path / "submodule"
    submodule.mkdir()
    _git("init", "-q", "-b", "main", cwd=submodule)
    _git("config", "user.email", "test@example.invalid", cwd=submodule)
    _git("config", "user.name", "Test", cwd=submodule)
    (submodule / "value.txt").write_text("one\n", encoding="utf-8")
    _git("add", "value.txt", cwd=submodule)
    _git("commit", "-qm", "initial", cwd=submodule)

    _git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(submodule),
        "declared-child",
        cwd=origin,
    )
    _git("commit", "-qm", "add child", cwd=origin)
    expected = _git("rev-parse", "HEAD", cwd=origin)
    _git("-c", "protocol.file.allow=always", "pull", cwd=registry)
    _git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--checkout",
        cwd=registry,
    )

    (submodule / "value.txt").write_text("two\n", encoding="utf-8")
    _git("commit", "-am", "advance", cwd=submodule)
    _git("fetch", cwd=registry / "declared-child")
    _git("checkout", "origin/main", cwd=registry / "declared-child")

    result = fetch_configured_registry(
        registry,
        configured_remote=str(origin),
        configured_ref="main",
        identity_file=identity,
        known_hosts_file=known_hosts,
    )

    assert result.revision == expected
    assert _git("rev-parse", "HEAD", cwd=registry) == expected
    assert _git("status", "--porcelain=v1", "--ignore-submodules=all", cwd=registry) == ""
    assert "declared-child" in _git("status", "--porcelain=v1", cwd=registry)


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
