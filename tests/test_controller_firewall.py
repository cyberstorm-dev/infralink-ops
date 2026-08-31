from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


def _deployment(registry: Path, uuid: str, body: str) -> None:
    path = registry / "hosts" / uuid / "operations"
    path.mkdir(parents=True)
    (path / "deployment.yml").write_text(body, encoding="utf-8")


def _commit_registry(registry: Path) -> str:
    for argv in (
        ["git", "init", str(registry)],
        ["git", "-C", str(registry), "config", "user.email", "tests@example.invalid"],
        ["git", "-C", str(registry), "config", "user.name", "Infralink tests"],
        ["git", "-C", str(registry), "add", "."],
        ["git", "-C", str(registry), "commit", "-m", "registry"],
    ):
        subprocess.run(argv, check=True, capture_output=True, text=True)
    return subprocess.run(
        ["git", "-C", str(registry), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_render_reports_a_host_without_firewall_as_disabled(tmp_path: Path) -> None:
    from infralink_ops.controller_firewall import main

    registry = tmp_path / "registry"
    uuid = "00000000-0000-4000-8000-000000000001"
    _deployment(registry, uuid, "services: []\n")
    revision = _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    payload, status = main(
        [
            "render",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 0
    assert payload == {
        "schema_version": "infralink.ops.firewall/v1",
        "ok": True,
        "command": {
            "path": ["render"],
            "args": {
                "registry": str(registry),
                "registry_revision": revision,
                "uuid": uuid,
                "compose": str(compose),
            },
        },
        "result": {"status": "disabled"},
        "next_actions": [],
        "meta": {"truncated": False},
    }


def test_render_rejects_a_registry_revision_mismatch(tmp_path: Path) -> None:
    from infralink_ops.controller_firewall import main

    registry = tmp_path / "registry"
    uuid = "00000000-0000-4000-8000-000000000001"
    _deployment(registry, uuid, "services: []\n")
    _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    payload, status = main(
        [
            "render",
            "--registry",
            str(registry),
            "--registry-revision",
            "0" * 40,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 78
    assert payload["error"] == {"code": "registry_checkout_failed"}


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
    revision = _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def verify(*, firewall: object, compose: bytes) -> None:
        observed["firewall"] = firewall
        observed["compose"] = compose

    monkeypatch.setattr(controller_firewall, "verify_firewall_policy", verify)
    monkeypatch.setattr(controller_firewall.socket, "if_nameindex", lambda: [(1, "tailscale0")])

    payload, status = controller_firewall.main(
        [
            "verify",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 0
    assert payload["result"] == {"status": "verified"}
    assert observed["compose"] == b"services: {}\n"


def test_render_rejects_a_missing_management_interface_before_emitting_rules(
    tmp_path: Path, monkeypatch
) -> None:
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
    interface: eth0
    sources: [100.64.0.0/10]
""",
    )
    revision = _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        controller_firewall.socket, "if_nameindex", lambda: [(1, "lo"), (2, "enp6s0")]
    )

    payload, status = controller_firewall.main(
        [
            "render",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 78
    assert payload["error"] == {
        "code": "firewall_management_interface_missing",
        "details": {
            "declared": "eth0",
            "observed": ["enp6s0", "lo"],
            "observed_count": 2,
        },
    }


def test_render_bounds_observed_interfaces_in_management_interface_failure(
    tmp_path: Path, monkeypatch
) -> None:
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
    interface: eth0
    sources: [100.64.0.0/10]
""",
    )
    revision = _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        controller_firewall.socket,
        "if_nameindex",
        lambda: [(index, f"if{index:02d}") for index in range(40)],
    )

    payload, status = controller_firewall.main(
        [
            "render",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 78
    assert payload["error"] == {
        "code": "firewall_management_interface_missing",
        "details": {
            "declared": "eth0",
            "observed": [f"if{index:02d}" for index in range(32)],
            "observed_count": 40,
        },
    }
    assert payload["meta"]["truncated"] is True


def test_verify_all_interface_management_ssh_without_enumerating_interfaces(
    tmp_path: Path, monkeypatch
) -> None:
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
    interface: any
    sources: [100.64.0.0/10]
""",
    )
    revision = _commit_registry(registry)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        controller_firewall.socket,
        "if_nameindex",
        lambda: (_ for _ in ()).throw(AssertionError("interface enumeration is unnecessary")),
    )
    monkeypatch.setattr(controller_firewall, "verify_firewall_policy", lambda **_kwargs: None)

    payload, status = controller_firewall.main(
        [
            "verify",
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            uuid,
            "--compose",
            str(compose),
        ]
    )

    assert status == 0
    assert payload["result"] == {"status": "verified"}


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
