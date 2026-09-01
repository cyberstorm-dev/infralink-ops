"""The controller doctor joins the one public Infralink command tree."""

from __future__ import annotations

import asyncio

from agent_surface.manifest import manifest_for

from infralink_ops import controller_doctor
from infralink_ops.controller_surface import build_app


def _healthy_payload() -> dict[str, object]:
    return {
        "schema_version": "infralink.controller-doctor/v1",
        "status": "healthy",
        "reason": "controller_reconcile_evidence_present",
        "evidence": {
            "host_uuid": "11111111-1111-4111-8111-111111111111",
            "controller_image": "ghcr.io/relax-dot-gg/infralink-controller:main",
            "controller": {"reference": None, "digest": None},
            "registry": {"path": "/var/lib/infralink/registry", "ref": "main", "head": None},
        },
    }


def test_controller_doctor_has_one_typed_app_projection(monkeypatch) -> None:
    monkeypatch.setattr(controller_doctor, "main", lambda argv=None: (_healthy_payload(), 0))

    result = asyncio.run(build_app().invoke("controller.doctor", {}))

    assert result.schema_version == "infralink.controller-doctor/v1"
    assert result.status == "healthy"


def test_controller_doctor_propagates_declared_registry_to_private_helper(monkeypatch) -> None:
    captured: list[str] = []

    def doctor(argv: list[str] | None = None) -> tuple[dict[str, object], int]:
        captured.extend(argv or [])
        return _healthy_payload(), 0

    monkeypatch.setattr(controller_doctor, "main", doctor)
    result = asyncio.run(
        build_app().invoke("controller.doctor", {"registry": "/var/lib/infralink/registry"})
    )

    assert result.status == "healthy"
    assert captured == ["--registry", "/var/lib/infralink/registry"]


def test_controller_surface_manifest_has_the_declared_entry_point() -> None:
    manifest = manifest_for(
        build_app(),
        factory="infralink_ops.controller_surface:build_app",
        distribution_name="infralink-ops",
        distribution_version="0.2.83",
    )

    assert [item["path"] for item in manifest["operations"]] == [["controller", "doctor"]]
