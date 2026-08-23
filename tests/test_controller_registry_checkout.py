from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

MODULE = "infralink_ops.controller_registry_checkout"


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


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def test_fetch_returns_the_detached_configured_registry_revision(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    (origin / "registry.yml").write_text("second\n", encoding="utf-8")
    _git("add", "registry.yml", cwd=origin)
    _git("commit", "-qm", "second", cwd=origin)
    expected_revision = _git("rev-parse", "HEAD", cwd=origin)

    completed = _run(
        "fetch",
        "--registry-root",
        str(registry),
        "--remote",
        str(origin),
        "--ref",
        "main",
        "--identity-file",
        str(identity),
        "--known-hosts-file",
        str(known_hosts),
    )

    assert completed.returncode == 0, completed.stderr
    payload = yaml.safe_load(completed.stdout)
    assert payload == {
        "schema_version": "infralink.ops.registry-checkout/v1",
        "ok": True,
        "command": {"path": ["fetch"], "args": {"registry_root": str(registry)}},
        "result": {"registry_root": str(registry.resolve()), "revision": expected_revision},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    assert _git("rev-parse", "HEAD", cwd=registry) == expected_revision


def test_fetch_rejects_dirty_runtime_cache_without_exposing_git_details(tmp_path: Path) -> None:
    origin, registry, identity, known_hosts = _checkout(tmp_path)
    (registry / "local-state.yml").write_text("must-not-be-preserved\n", encoding="utf-8")

    completed = _run(
        "fetch",
        "--registry-root",
        str(registry),
        "--remote",
        str(origin),
        "--ref",
        "main",
        "--identity-file",
        str(identity),
        "--known-hosts-file",
        str(known_hosts),
    )

    assert completed.returncode == 78, completed.stderr
    assert completed.stderr == ""
    payload = yaml.safe_load(completed.stdout)
    assert payload == {
        "schema_version": "infralink.ops.registry-checkout/v1",
        "ok": False,
        "command": {"path": ["fetch"], "args": {"registry_root": str(registry)}},
        "error": {"code": "registry_checkout_failed"},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    assert (registry / "local-state.yml").read_text(encoding="utf-8") == "must-not-be-preserved\n"
