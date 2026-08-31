"""The controller must join the public Infralink agent surface."""

from __future__ import annotations

import json

from click.testing import CliRunner

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
