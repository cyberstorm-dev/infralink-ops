"""Process boundary for typed private controller environment adapters."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from infralink.controller_contracts import ControllerAdapterRequest
from pydantic import ValidationError

from infralink_ops.controller_adapter import (
    ControllerAdapterTransportError,
    invoke_controller_adapter,
)

SCHEMA_VERSION = "infralink.ops.controller-adapter-transport/v1"


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid command usage inside the machine-readable response."""

    def error(self, message: str) -> None:
        raise ValueError(message)


def _payload(
    *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = error
    return payload


def main(argv: list[str] | None = None, *, stdin: str | None = None) -> tuple[dict[str, Any], int]:
    """Invoke one explicit environment adapter with a typed stdin request."""

    parser = EnvelopeParser(prog="infralink-controller-adapter")
    commands = parser.add_subparsers(dest="command", required=True)
    invoke = commands.add_parser("invoke")
    invoke.add_argument("--adapter", required=True)
    invoke.add_argument("--adapter-arg", action="append", default=[])
    invoke.add_argument("--diagnostic-file", type=Path)
    try:
        arguments = parser.parse_args(argv)
    except (SystemExit, ValueError):
        return _payload(error={"code": "usage_error"}), 64

    request_body = sys.stdin.read() if stdin is None else stdin
    try:
        request = ControllerAdapterRequest.model_validate_json(request_body)
    except (ValidationError, ValueError):
        return _payload(error={"code": "request_invalid"}), 64

    try:
        result = invoke_controller_adapter(
            [arguments.adapter, *arguments.adapter_arg],
            request,
            diagnostic_file=arguments.diagnostic_file,
        )
    except ControllerAdapterTransportError as error:
        details: dict[str, Any] = {"code": error.category}
        if error.returncode is not None:
            details["exit_code"] = error.returncode
        if error.diagnostic_code is not None:
            details["diagnostic_code"] = error.diagnostic_code
        return _payload(error=details), 78
    except ValueError:
        return _payload(error={"code": "adapter_transport_failed"}), 78
    return _payload(result=result.model_dump(mode="json")), 0


def cli() -> int:
    """Write exactly one JSON transport envelope."""

    payload, status = main()
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
