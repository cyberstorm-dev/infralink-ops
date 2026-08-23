import json
import subprocess
from pathlib import Path

import pytest

from infralink_ops.controller_render_secrets import (
    RenderSecretsError,
    resolve,
)

HOST_ID = "11111111-1111-4111-8111-111111111111"


def _registry(tmp_path: Path, *, required: bool = True) -> Path:
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
    return registry


def test_resolves_declared_binding_as_shell_safe_export(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _registry(tmp_path)

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert argv == ["bws", "secret", "list", "project-id", "--output", "json"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps([{"key": "api_token", "value": "space value"}]),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert resolve(registry=registry, host_id=HOST_ID) == ["API_TOKEN='space value'"]


def test_missing_required_binding_fails_closed(tmp_path: Path, monkeypatch) -> None:
    registry = _registry(tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 0, stdout="[]"),
    )

    with pytest.raises(RenderSecretsError, match="required_secret_missing"):
        resolve(registry=registry, host_id=HOST_ID)
