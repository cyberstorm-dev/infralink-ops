"""Typed subprocess transport for controller environment adapters."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from infralink.controller_contracts import ControllerAdapterRequest, ControllerAdapterResult
from pydantic import ValidationError

_DIAGNOSTIC_CODE_PREFIX = "infralink-adapter-diagnostic: "
_DIAGNOSTIC_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_DIAGNOSTIC_BYTES = 64 * 1024


class ControllerAdapterTransportError(RuntimeError):
    """A controller adapter could not return a valid public result."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        category: str = "adapter_transport_failed",
        diagnostic_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.category = category
        self.diagnostic_code = diagnostic_code


def _diagnostic_code(stderr: str) -> str | None:
    """Return an exact allowlisted private-adapter diagnostic code."""

    for line in stderr.splitlines():
        candidate = line.strip()
        if not candidate.startswith(_DIAGNOSTIC_CODE_PREFIX):
            continue
        code = candidate.removeprefix(_DIAGNOSTIC_CODE_PREFIX)
        if _DIAGNOSTIC_CODE.fullmatch(code) is not None:
            return code
    return None


def _write_diagnostic(path: Path, stderr: str) -> None:
    """Atomically retain bounded private stderr outside the public envelope."""

    content = stderr.encode("utf-8", errors="surrogateescape")
    if len(content) > _MAX_DIAGNOSTIC_BYTES:
        content = b"[truncated to final 65536 bytes]\n" + content[-_MAX_DIAGNOSTIC_BYTES:]
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with open(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
        Path(temporary).chmod(0o600)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def invoke_controller_adapter(
    adapter_argv: Sequence[str],
    request: ControllerAdapterRequest,
    *,
    diagnostic_file: Path | None = None,
) -> ControllerAdapterResult:
    """Run a fixed adapter argv and validate its JSON result contract.

    Only an allowlisted diagnostic code may cross the private adapter boundary;
    adapter text remains private.
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
        if diagnostic_file is not None:
            try:
                _write_diagnostic(diagnostic_file, completed.stderr)
            except OSError:
                pass
        raise ControllerAdapterTransportError(
            "adapter invocation failed",
            returncode=completed.returncode,
            category="adapter_exit_nonzero",
            diagnostic_code=_diagnostic_code(completed.stderr),
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
