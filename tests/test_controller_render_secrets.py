import json
import subprocess
from pathlib import Path

import pytest

from infralink_ops.controller_render_secrets import (
    RenderSecretsError,
    resolve,
)

HOST_ID = "11111111-1111-4111-8111-111111111111"


def _registry(tmp_path: Path, *, required: bool = True) -> tuple[Path, str]:
    registry = tmp_path / "registry"
    deployment = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    deployment.write_text("render_secrets:\n  path: secrets/render.yml\n", encoding="utf-8")
    secrets = registry / "secrets" / "render.yml"
    secrets.parent.mkdir()
    secrets.write_text(
        "projects:\n"
        "  - alias: runtime\n"
        "    project_id: project-id\n"
        "bindings:\n"
        "  - context_key: API_TOKEN\n"
        "    project: runtime\n"
        "    secret_key: api_token\n"
        f"    required: {str(required).lower()}\n",
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
    return registry, revision


def test_resolves_declared_binding_as_shell_safe_export(tmp_path: Path, monkeypatch) -> None:
    registry, revision = _registry(tmp_path)
    real_run = subprocess.run

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != "bws":
            return real_run(argv, **_)
        assert argv == ["bws", "secret", "list", "project-id", "--output", "json"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps([{"key": "api_token", "value": "space value"}]),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert resolve(registry=registry, registry_revision=revision, host_id=HOST_ID) == [
        "API_TOKEN='space value'"
    ]


def test_missing_required_binding_fails_closed(tmp_path: Path, monkeypatch) -> None:
    registry, revision = _registry(tmp_path)
    real_run = subprocess.run

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_: (
            subprocess.CompletedProcess(argv, 0, stdout="[]")
            if argv[0] == "bws"
            else real_run(argv, **_)
        ),
    )

    with pytest.raises(RenderSecretsError, match="required_secret_missing"):
        resolve(registry=registry, registry_revision=revision, host_id=HOST_ID)


def test_rejects_a_dirty_or_revision_mismatched_registry(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)

    with pytest.raises(RenderSecretsError, match="registry_checkout_failed"):
        resolve(registry=registry, registry_revision="0" * 40, host_id=HOST_ID)

    (registry / "dirty.txt").write_text("not desired state\n", encoding="utf-8")

    with pytest.raises(RenderSecretsError, match="registry_checkout_failed"):
        resolve(registry=registry, registry_revision=revision, host_id=HOST_ID)
