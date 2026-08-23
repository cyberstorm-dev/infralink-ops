"""Materialize explicit SSH registry transport trust without selecting registry state."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "infralink.ops.registry-transport-trust/v1"


class RegistryTransportTrustError(ValueError):
    """Registry SSH trust input cannot be safely materialized."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid invocation in the machine-readable response."""

    def error(self, message: str) -> None:
        raise RegistryTransportTrustError("usage_error")


def materialize_registry_transport_trust(*, content: str, destination: Path) -> None:
    """Atomically replace one bootstrap-owned known-hosts file with mode 0600."""

    if not content.strip() or "\x00" in content:
        raise RegistryTransportTrustError("registry_transport_trust_invalid")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise RegistryTransportTrustError("registry_transport_trust_destination_invalid")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-known_hosts.", dir=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content if content.endswith("\n") else f"{content}\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except OSError as error:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise RegistryTransportTrustError("registry_transport_trust_write_failed") from error


def _payload(*, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Read trust from stdin and materialize it to one explicit destination."""

    parser = EnvelopeParser(prog="infralink-controller-registry-transport-trust")
    parser.add_argument("apply", choices=("apply",))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--stdin", action="store_true")
    try:
        arguments = parser.parse_args(argv)
        if not arguments.stdin:
            raise RegistryTransportTrustError("usage_error")
        materialize_registry_transport_trust(
            content=sys.stdin.read(), destination=arguments.destination
        )
        return _payload(result={"path": str(arguments.destination), "mode": "0600"}), 0
    except RegistryTransportTrustError as error:
        return _payload(error=str(error)), 64 if str(error) == "usage_error" else 78


def cli() -> int:
    """Write the trust-materialization envelope as YAML."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
