"""Controller runnable for explicit public firewall render requests."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path
from typing import Any

import yaml

from infralink_ops.firewall import (
    FirewallError,
    load_firewall_policy,
    render_firewall_policy,
    verify_firewall_policy,
)
from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision

SCHEMA_VERSION = "infralink.ops.firewall/v1"
MAX_OBSERVED_INTERFACES = 32


class ManagementInterfaceError(FirewallError):
    """A declared management interface is absent from the live host."""

    def __init__(self, *, declared: str, observed: list[str], observed_count: int) -> None:
        super().__init__("firewall_management_interface_missing")
        self.declared = declared
        self.observed = observed
        self.observed_count = observed_count


class EnvelopeParser(argparse.ArgumentParser):
    """Return invalid invocation as an envelope instead of argparse text."""

    def error(self, message: str) -> None:
        raise FirewallError("usage_error")


def _payload(
    *,
    command: str | None,
    registry: Path | None,
    registry_revision: str | None,
    uuid: str | None,
    compose: Path | None,
    result: dict[str, object] | None = None,
    error: str | None = None,
    error_details: dict[str, object] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    arguments: dict[str, str] = {}
    if registry is not None:
        arguments["registry"] = str(registry)
    if registry_revision is not None:
        arguments["registry_revision"] = registry_revision
    if uuid is not None:
        arguments["uuid"] = uuid
    if compose is not None:
        arguments["compose"] = str(compose)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command else [], "args": arguments},
        "next_actions": [],
        "meta": {"truncated": truncated},
    }
    if error is None:
        payload["result"] = result or {"status": "disabled"}
    else:
        payload["error"] = {"code": error}
        if error_details is not None:
            payload["error"]["details"] = error_details
    return payload


def _validate_management_interface(*, interface: str) -> None:
    """Reject a concrete management interface absent from the live namespace."""

    if interface == "any":
        return
    all_observed = sorted({name for _, name in socket.if_nameindex()})
    if interface not in all_observed:
        raise ManagementInterfaceError(
            declared=interface,
            observed=all_observed[:MAX_OBSERVED_INTERFACES],
            observed_count=len(all_observed),
        )


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Render one explicitly addressed host firewall declaration."""

    parser = EnvelopeParser(prog="infralink-controller-firewall")
    parser.add_argument("command", choices=("render", "verify"))
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--registry-revision", required=True)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--compose", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except FirewallError as error:
        return _payload(
            command=None,
            registry=None,
            registry_revision=None,
            uuid=None,
            compose=None,
            error=str(error),
        ), 64
    try:
        checkout = verify_registry_revision(args.registry, expected_revision=args.registry_revision)
        deployment = checkout.root / "hosts" / args.uuid / "operations" / "deployment.yml"
        firewall = load_firewall_policy(deployment)
        if firewall is None:
            result: dict[str, object] = {"status": "disabled"}
        else:
            _validate_management_interface(interface=firewall.management_ssh.interface)
            if args.command == "verify":
                verify_firewall_policy(firewall=firewall, compose=args.compose.read_bytes())
                result = {"status": "verified"}
            else:
                result = {
                    "status": "rendered",
                    "rules": render_firewall_policy(
                        firewall=firewall, compose=args.compose.read_bytes()
                    ).decode("utf-8"),
                }
        return _payload(
            command=args.command,
            registry=checkout.root,
            registry_revision=checkout.revision,
            uuid=args.uuid,
            compose=args.compose,
            result=result,
        ), 0
    except RegistryCheckoutError:
        return _payload(
            command=args.command,
            registry=args.registry,
            registry_revision=args.registry_revision,
            uuid=args.uuid,
            compose=args.compose,
            error="registry_checkout_failed",
        ), 78
    except ManagementInterfaceError as error:
        return _payload(
            command=args.command,
            registry=args.registry,
            registry_revision=args.registry_revision,
            uuid=args.uuid,
            compose=args.compose,
            error=str(error),
            error_details={
                "declared": error.declared,
                "observed": error.observed,
                "observed_count": error.observed_count,
            },
            truncated=error.observed_count > len(error.observed),
        ), 78
    except (OSError, UnicodeDecodeError, FirewallError):
        return _payload(
            command=args.command,
            registry=args.registry,
            registry_revision=args.registry_revision,
            uuid=args.uuid,
            compose=args.compose,
            error="firewall_render_failed",
        ), 78


def cli() -> int:
    """Write one YAML controller response envelope."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
