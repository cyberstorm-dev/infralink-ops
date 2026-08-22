"""Typed subprocess transport for controller environment adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from infralink.controller_contracts import ControllerAdapterRequest, ControllerAdapterResult
from pydantic import ValidationError


class ControllerAdapterTransportError(RuntimeError):
    """A controller adapter could not return a valid public result."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


def invoke_controller_adapter(
    adapter_argv: Sequence[str], request: ControllerAdapterRequest
) -> ControllerAdapterResult:
    """Run a fixed adapter argv and validate its JSON result contract.

    Adapter diagnostics are intentionally not copied into the exception because
    their implementation belongs to the private environment boundary.
    """

    if not adapter_argv:
        raise ValueError("adapter argv must not be empty")

    try:
        completed = subprocess.run(
            list(adapter_argv),
            input=request.model_dump_json(),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise ControllerAdapterTransportError("adapter could not be started") from error

    if completed.returncode != 0:
        raise ControllerAdapterTransportError(
            "adapter invocation failed", returncode=completed.returncode
        )

    try:
        result = ControllerAdapterResult.model_validate_json(completed.stdout)
    except (ValidationError, ValueError) as error:
        raise ControllerAdapterTransportError("adapter returned invalid result") from error

    if result.registry_revision != request.registry_revision:
        raise ControllerAdapterTransportError("adapter result registry revision mismatch")
    if result.phase != request.phase:
        raise ControllerAdapterTransportError("adapter result phase mismatch")
    return result
