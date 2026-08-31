"""The controller must join the public Infralink agent surface."""

from __future__ import annotations

import json

import yaml
from click.testing import CliRunner
from infralink.cli.main import cli
from infralink.mcp_server import _native_paths, invoke_cli

from infralink_ops import controller_doctor
from infralink_ops.controller_surface import controller_command


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

    result = CliRunner().invoke(controller_command(), ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["controller", "doctor"]
    assert payload["result"]["status"] == "healthy"


def test_controller_doctor_is_discovered_by_the_native_mcp_projection() -> None:
    assert _native_paths()["infralink_controller_doctor"] == ("controller", "doctor")

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
