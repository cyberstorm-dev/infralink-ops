"""The controller must join the public Infralink agent surface."""

from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner
from infralink.cli.main import cli
from infralink.mcp_server import _native_paths, _native_tool, invoke_cli

from infralink_ops import controller_doctor


def test_controller_doctor_projects_the_private_probe_through_infralink(monkeypatch) -> None:
    monkeypatch.setattr(
        controller_doctor,
        "main",
        lambda argv=None: (
            {
                "schema_version": "infralink.controller-doctor/v1",
                "status": "healthy",
                "reason": "controller_reconcile_evidence_present",
                "evidence": {
                    "host_uuid": "11111111-1111-4111-8111-111111111111",
                    "controller_image": "ghcr.io/cyberstorm-dev/infralink-ops-controller:main",
                    "controller": {"reference": None, "digest": None},
                    "registry": {
                        "path": "/var/lib/infralink/registry",
                        "ref": "main",
                        "head": None,
                    },
                },
            },
            0,
        ),
    )

    result = CliRunner().invoke(cli, ["--output", "json", "controller", "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["controller", "doctor"]
    assert payload["result"]["status"] == "healthy"


def test_controller_doctor_is_discovered_by_the_native_mcp_projection() -> None:
    assert _native_paths()["infralink_controller_doctor"] == ("controller", "doctor")
    tool = _native_tool("infralink_controller_doctor", ("controller", "doctor"))
    assert {"format", "yaml_style"}.isdisjoint(tool.input_schema["properties"])
    assert {"registry", "edges"} <= set(tool.input_schema["properties"])

    payload = invoke_cli(["controller", "doctor"])

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["command"]["raw"] == "infralink --output json controller doctor"
    assert payload["command"]["resolved"]["output"] == "json"


def test_controller_doctor_inherits_an_explicit_root_json_selection() -> None:
    result = CliRunner().invoke(cli, ["--output", "json", "controller", "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"]["raw"] == "infralink --output json controller doctor"
    assert payload["command"]["resolved"]["output"] == "json"


def test_controller_doctor_keeps_yaml_as_the_default_output() -> None:
    result = CliRunner().invoke(cli, ["controller", "doctor"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["command"]["raw"] == "infralink controller doctor"
    assert payload["command"]["resolved"]["output"] == "yaml"


@pytest.mark.parametrize(
    ("argv", "json_output"),
    [
        (["controller", "doctor", "--format", "json"], False),
        (["--output", "json", "controller", "doctor", "--format", "yaml"], True),
        (["controller", "doctor", "--yaml-style", "flow"], False),
    ],
)
def test_controller_doctor_rejects_a_child_output_override(
    argv: list[str], json_output: bool
) -> None:
    result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 2
    payload = json.loads(result.output) if json_output else yaml.safe_load(result.output)
    assert payload["error"]["code"] == "usage_error"
    assert "belongs to the root infralink command" in payload["error"]["message"]


def test_controller_doctor_inherits_the_root_registry_selector(monkeypatch) -> None:
    missing_registry = "/definitely/not/a/registry"
    captured: list[str] = []

    def capture(argv: list[str] | None = None) -> tuple[dict[str, object], int]:
        captured.extend(argv or [])
        return (
            {
                "schema_version": "infralink.controller-doctor/v1",
                "status": "healthy",
                "reason": "controller_reconcile_evidence_present",
                "evidence": {},
            },
            0,
        )

    monkeypatch.setattr(controller_doctor, "main", capture)
    result = CliRunner().invoke(cli, ["--registry", missing_registry, "controller", "doctor"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["command"]["resolved"]["registry"] == missing_registry
    assert captured == ["--registry", missing_registry]


def test_controller_doctor_rejects_a_child_registry_override() -> None:
    result = CliRunner().invoke(cli, ["controller", "doctor", "--registry", "/tmp/registry"])

    assert result.exit_code == 2
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "usage_error"
    assert "--registry belongs to the root" in payload["error"]["message"]
