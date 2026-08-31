"""Typed controller operations exposed only through the ``infralink`` command tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import click
from agent_surface import App, OperationError
from agent_surface.adapters.click import ClickAdapter
from infralink.agent_surface import InfralinkEnvelopeRenderer
from pydantic import BaseModel, ConfigDict

from infralink_ops import controller_doctor


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControllerDoctorRequest(_Contract):
    host_env: Path | None = None
    registry: Path | None = None
    registry_key: Path | None = None
    runtime_dir: Path | None = None
    services_dir: Path | None = None
    textfile_directory: Path | None = None
    docker: str = "docker"


class ControllerDoctorResult(_Contract):
    schema_version: Literal["infralink.controller-doctor/v1"]
    status: Literal["healthy", "unhealthy", "unknown"]
    reason: str
    evidence: dict[str, Any]


controller_surface = App("infralink")


@controller_surface.operation(
    "controller.doctor",
    summary="Inspect controller-local convergence evidence",
    read_only=True,
)  # type: ignore[untyped-decorator]
def controller_doctor_operation(request: ControllerDoctorRequest) -> ControllerDoctorResult:
    """Run the private controller doctor without publishing a second executable."""
    argv: list[str] = []
    for name, value in (
        ("host_env", request.host_env),
        ("registry", request.registry),
        ("registry_key", request.registry_key),
        ("runtime_dir", request.runtime_dir),
        ("services_dir", request.services_dir),
        ("textfile_directory", request.textfile_directory),
    ):
        if value is not None:
            argv.extend(("--" + name.replace("_", "-"), str(value)))
    if request.docker != "docker":
        argv.extend(("--docker", request.docker))
    payload, _status = controller_doctor.main(argv)
    try:
        return ControllerDoctorResult.model_validate(payload)
    except ValueError as error:
        raise OperationError(
            "controller_doctor_contract_invalid",
            "Private controller doctor returned an invalid contract",
            fix="Inspect the controller image and retry the declared host operation.",
        ) from error


def controller_command() -> click.Command:
    """Return the ``controller`` child generated from the typed operation registry."""
    root = ClickAdapter(controller_surface, envelope_renderer=InfralinkEnvelopeRenderer()).command()
    command = root.get_command(click.Context(root), "controller")
    if command is None:
        raise RuntimeError("controller_command_missing")
    return command
