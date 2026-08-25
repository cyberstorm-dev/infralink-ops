from __future__ import annotations

import subprocess
from pathlib import Path

from infralink_ops.template_source_dependencies import main

UUID = "11111111-1111-1111-1111-111111111111"


def _git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_registry(registry: Path) -> str:
    _git("init", "-q", cwd=registry)
    _git("config", "user.email", "tests@example.invalid", cwd=registry)
    _git("config", "user.name", "Infralink tests", cwd=registry)
    _git("add", ".", cwd=registry)
    _git("commit", "-qm", "registry", cwd=registry)
    return _git("rev-parse", "HEAD", cwd=registry)


def _write_manifest(registry: Path, source: str) -> None:
    manifest = registry / "hosts" / UUID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {UUID}:\n"
        "    template_sources:\n"
        "      - id: shared-config\n"
        f"        source: {source}\n",
        encoding="utf-8",
    )


def _discover(registry: Path, revision: str) -> tuple[dict[str, object], int]:
    return main(
        [
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            UUID,
        ]
    )


def test_returns_no_gitlink_for_regular_declared_template_source(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _write_manifest(registry, "shared/regular-config")
    (registry / "shared" / "regular-config").mkdir(parents=True)
    revision = _commit_registry(registry)

    payload, status = _discover(registry, revision)

    assert status == 0
    assert payload["result"] == {"template_source_submodules": []}


def test_returns_declared_top_level_gitlink_for_source_child(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _write_manifest(registry, "shared/declared-source/templates")
    revision = _commit_registry(registry)
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'a' * 40},shared/declared-source",
        ],
        cwd=registry,
        check=True,
    )
    _git("commit", "-qm", "declared source", cwd=registry)
    revision = _git("rev-parse", "HEAD", cwd=registry)

    payload, status = _discover(registry, revision)

    assert status == 0
    assert payload["result"] == {"template_source_submodules": ["shared/declared-source"]}


def test_rejects_registry_revision_mismatch_before_discovery(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _write_manifest(registry, "shared/regular-config")
    (registry / "shared" / "regular-config").mkdir(parents=True)
    _commit_registry(registry)

    payload, status = _discover(registry, "0" * 40)

    assert status == 78
    assert payload["error"] == {"code": "registry_revision_mismatch"}


def test_rejects_host_reference_outside_registry_hosts_root(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    _write_manifest(registry, "shared/regular-config")
    (registry / "shared" / "regular-config").mkdir(parents=True)
    outside = registry / "outside" / "manifest.yml"
    outside.parent.mkdir()
    outside.write_text(
        "hosts:\n  ../outside:\n    template_sources: []\n",
        encoding="utf-8",
    )
    revision = _commit_registry(registry)

    payload, status = main(
        [
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            "../outside",
        ]
    )

    assert status == 78
    assert payload["error"] == {"code": "template_source_manifest_invalid"}
