from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


def _deployment(registry: Path, uuid: str, body: str) -> None:
    path = registry / "hosts" / uuid / "operations"
    path.mkdir(parents=True)
    (path / "deployment.yml").write_text(body, encoding="utf-8")


def test_render_reports_a_host_without_firewall_as_disabled(tmp_path: Path) -> None:
    from infralink_ops.controller_firewall import main

    registry = tmp_path / "registry"
    uuid = "00000000-0000-4000-8000-000000000001"
    _deployment(registry, uuid, "services: []\n")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    payload, status = main(
        ["render", "--registry", str(registry), "--uuid", uuid, "--compose", str(compose)]
    )

    assert status == 0
    assert payload == {
        "schema_version": "infralink.ops.firewall/v1",
        "ok": True,
        "command": {
            "path": ["render"],
            "args": {"registry": str(registry), "uuid": uuid, "compose": str(compose)},
        },
        "result": {"status": "disabled"},
        "next_actions": [],
        "meta": {"truncated": False},
    }


def test_verify_invokes_public_runtime_for_a_declared_firewall(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_firewall as controller_firewall

    registry = tmp_path / "registry"
    uuid = "00000000-0000-4000-8000-000000000001"
    _deployment(
        registry,
        uuid,
        """firewall:
  backend: nftables
  mode: default-deny
  management_ssh:
    port: 22
    interface: tailscale0
    sources: [100.64.0.0/10]
""",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def verify(*, firewall: object, compose: bytes) -> None:
        observed["firewall"] = firewall
        observed["compose"] = compose

    monkeypatch.setattr(controller_firewall, "verify_firewall_policy", verify)

    payload, status = controller_firewall.main(
        ["verify", "--registry", str(registry), "--uuid", uuid, "--compose", str(compose)]
    )

    assert status == 0
    assert payload["result"] == {"status": "verified"}
    assert observed["compose"] == b"services: {}\n"


def test_module_emits_yaml_usage_envelope() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "infralink_ops.controller_firewall"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert completed.stderr == ""
    assert yaml.safe_load(completed.stdout)["error"] == {"code": "usage_error"}
