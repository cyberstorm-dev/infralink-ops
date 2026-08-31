"""Controller runnable for explicit public firewall render requests."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path
from subprocess import run
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


class FirewallPreflightError(FirewallError):
    """A host listener declaration does not match the live network namespace."""

    def __init__(
        self, code: str, *, details: dict[str, object] | None = None, truncated: bool = False
    ) -> None:
        super().__init__(code)
        self.details = details
        self.truncated = truncated


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


def _interface_addresses() -> dict[str, list[str]]:
    """Return concrete host-namespace addresses owned by each interface."""

    try:
        completed = run(
            ["nsenter", "-t", "1", "-n", "ip", "-j", "address", "show"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise FirewallPreflightError("firewall_network_inventory_unavailable") from error
    if completed.returncode != 0:
        raise FirewallPreflightError("firewall_network_inventory_unavailable")
    try:
        entries = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise FirewallPreflightError("firewall_network_inventory_unavailable") from error
    if not isinstance(entries, list):
        raise FirewallPreflightError("firewall_network_inventory_unavailable")

    result: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise FirewallPreflightError("firewall_network_inventory_unavailable")
        interface = entry.get("ifname")
        address_info = entry.get("addr_info")
        if not isinstance(interface, str) or not isinstance(address_info, list):
            raise FirewallPreflightError("firewall_network_inventory_unavailable")
        addresses: list[str] = []
        for address in address_info:
            if not isinstance(address, dict) or not isinstance(address.get("local"), str):
                raise FirewallPreflightError("firewall_network_inventory_unavailable")
            try:
                addresses.append(str(ipaddress.ip_address(address["local"])))
            except ValueError as error:
                raise FirewallPreflightError("firewall_network_inventory_unavailable") from error
        result[interface] = sorted(set(addresses))
    return result


def _bounded(values: list[str]) -> tuple[list[str], int, bool]:
    """Bound list-valued error evidence while retaining its total count."""

    return values[:MAX_OBSERVED_INTERFACES], len(values), len(values) > MAX_OBSERVED_INTERFACES


def _validate_host_listeners(*, firewall: object) -> None:
    """Reject host-bound firewall listeners absent from the live namespace."""

    management = firewall.management_ssh
    ingress = firewall.ingress
    if management.interface == "any" and not ingress:
        return
    addresses_by_interface = _interface_addresses()
    observed_interfaces = sorted(addresses_by_interface)
    bounded_interfaces, interface_count, interface_truncated = _bounded(observed_interfaces)

    if management.interface != "any" and management.interface not in observed_interfaces:
        raise FirewallPreflightError(
            "firewall_management_interface_missing",
            details={
                "declared": management.interface,
                "observed": bounded_interfaces,
                "observed_count": interface_count,
            },
            truncated=interface_truncated,
        )
    for rule in ingress:
        if rule.interface not in observed_interfaces:
            raise FirewallPreflightError(
                "firewall_ingress_interface_missing",
                details={
                    "service": rule.service,
                    "declared": rule.interface,
                    "observed": bounded_interfaces,
                    "observed_count": interface_count,
                },
                truncated=interface_truncated,
            )
    for rule in ingress:
        observed_addresses = addresses_by_interface.get(rule.interface, [])
        bounded_addresses, address_count, address_truncated = _bounded(observed_addresses)
        if rule.bind_address not in observed_addresses:
            raise FirewallPreflightError(
                "firewall_ingress_bind_address_missing",
                details={
                    "service": rule.service,
                    "interface": rule.interface,
                    "bind_address": rule.bind_address,
                    "observed": bounded_addresses,
                    "observed_count": address_count,
                },
                truncated=address_truncated,
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
            _validate_host_listeners(firewall=firewall)
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
    except FirewallPreflightError as error:
        return _payload(
            command=args.command,
            registry=args.registry,
            registry_revision=args.registry_revision,
            uuid=args.uuid,
            compose=args.compose,
            error=str(error),
            error_details=error.details,
            truncated=error.truncated,
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
