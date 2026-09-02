"""Render public Infralink firewall declarations without environment policy."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from infralink.firewall import FirewallPolicy
from pydantic import ValidationError


class FirewallError(ValueError):
    """A public firewall declaration or Compose exposure is unsafe."""


@dataclass(frozen=True)
class _PublishedPort:
    service: str
    protocol: str
    host_address: str
    port: int
    target_port: int


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def load_firewall_policy(deployment: Path) -> FirewallPolicy | None:
    """Load the optional portable firewall declaration from one deployment file."""

    try:
        document = yaml.safe_load(deployment.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise FirewallError("firewall_declaration_unavailable") from error
    if not isinstance(document, Mapping):
        raise FirewallError("firewall_declaration_invalid")
    declaration = document.get("firewall")
    if declaration is None:
        return None
    try:
        return FirewallPolicy.model_validate(declaration)
    except ValidationError as error:
        raise FirewallError("firewall_declaration_invalid") from error


def _parse_short_port(value: str) -> tuple[str, str | None, object, object]:
    value, separator, protocol = value.partition("/")
    if separator and protocol not in {"tcp", "udp"}:
        raise FirewallError("compose_port_protocol_unsupported")
    fields = value.rsplit(":", 2)
    if len(fields) == 2:
        return protocol or "tcp", None, fields[0], fields[1]
    if len(fields) == 3:
        host_address, published, target = fields
        if host_address.startswith("[") and host_address.endswith("]"):
            host_address = host_address[1:-1]
        return protocol or "tcp", host_address, published, target
    raise FirewallError("compose_port_unsupported")


def _published_ports(compose: bytes) -> tuple[list[_PublishedPort], set[str], set[str]]:
    try:
        document = yaml.safe_load(compose)
        services = document["services"]
        if not isinstance(services, Mapping):
            raise TypeError
    except (KeyError, TypeError, yaml.YAMLError) as error:
        raise FirewallError("rendered_compose_invalid") from error

    result: list[_PublishedPort] = []
    services_seen: set[str] = set()
    host_networked: set[str] = set()
    for service, definition in services.items():
        if not isinstance(service, str) or not isinstance(definition, Mapping):
            raise FirewallError("rendered_compose_invalid")
        services_seen.add(service)
        if definition.get("network_mode") == "host":
            if definition.get("ports"):
                raise FirewallError("host_networking_compose_ports_conflict")
            host_networked.add(service)
            continue
        for item in definition.get("ports", []) or []:
            protocol = "tcp"
            published: object
            target: object
            host_ip: object = None
            if isinstance(item, str):
                protocol, host_ip, published, target = _parse_short_port(item)
            elif isinstance(item, Mapping):
                published = item.get("published")
                target = item.get("target")
                host_ip = item.get("host_ip")
                protocol = item.get("protocol", "tcp")
            else:
                raise FirewallError("compose_port_unsupported")
            if protocol not in {"tcp", "udp"}:
                raise FirewallError("compose_port_protocol_unsupported")
            try:
                port = int(published)
                target_port = int(target)
            except (TypeError, ValueError):
                raise FirewallError("compose_published_port_invalid") from None
            if not 1 <= port <= 65535 or not 1 <= target_port <= 65535:
                raise FirewallError("compose_published_port_invalid")
            if host_ip in {"127.0.0.1", "::1"}:
                continue
            if not isinstance(host_ip, str):
                raise FirewallError("compose_published_host_address_required")
            try:
                address = ipaddress.ip_address(host_ip)
            except ValueError:
                raise FirewallError("compose_published_host_address_invalid") from None
            if address.is_unspecified or address.is_loopback or str(address) != host_ip:
                raise FirewallError("compose_published_host_address_invalid")
            result.append(_PublishedPort(service, protocol, host_ip, port, target_port))
    return result, services_seen, host_networked


def _source_expression(source: str) -> str:
    network = ipaddress.ip_network(source)
    return f"ip{'6' if network.version == 6 else ''} saddr {network}"


def _destination_expression(destination: str) -> str:
    address = ipaddress.ip_address(destination)
    return f"ip{'6' if address.version == 6 else ''} daddr {address}"


def _interface_expression(interface: str) -> str:
    return "" if interface == "any" else f'iifname "{interface}" '


def render_firewall_policy(*, firewall: FirewallPolicy, compose: bytes) -> bytes:
    """Validate Compose publication and render the owned nftables policy table."""

    if type(firewall) is not FirewallPolicy or type(compose) is not bytes:
        raise FirewallError("firewall_inputs_invalid")
    external, services_seen, host_networked = _published_ports(compose)
    if firewall.container_egress:
        raise FirewallError("container_egress_unsupported")
    observed = {
        (entry.service, entry.protocol, entry.host_address, entry.port) for entry in external
    }
    if len(observed) != len(external):
        raise FirewallError("compose_published_port_ownership_duplicate")

    declared: set[tuple[str, str, str, int]] = set()
    ingress_rules: list[str] = []
    for ingress in firewall.ingress:
        for port in ingress.ports:
            declared.add((ingress.service, ingress.protocol, ingress.bind_address, port))
            for source in ingress.sources:
                ingress_rules.append(
                    f'    iifname "{ingress.interface}" {_source_expression(source)} '
                    f"{_destination_expression(ingress.bind_address)} "
                    f"{ingress.protocol} dport {port} accept"
                )
    ingress_rules = list(dict.fromkeys(ingress_rules))
    if not {rule.service for rule in firewall.ingress}.issubset(services_seen):
        raise FirewallError("firewall_ingress_service_absent")

    policies: dict[tuple[str, str, int], tuple[str, ...]] = {}
    for ingress in firewall.ingress:
        sources = tuple(sorted(ingress.sources))
        for port in ingress.ports:
            endpoint = (ingress.protocol, ingress.interface, port)
            previous = policies.setdefault(endpoint, sources)
            if previous != sources:
                raise FirewallError("multi_address_ingress_policy_ambiguous")

    published_declarations = {entry for entry in declared if entry[0] not in host_networked}
    if observed != published_declarations:
        raise FirewallError("compose_published_port_ownership_mismatch")

    ingress_by_publication = {
        (ingress.service, ingress.protocol, ingress.bind_address, port): ingress
        for ingress in firewall.ingress
        for port in ingress.ports
    }
    # DNAT discards the destination address before the forward hook, but the
    # incoming interface remains available. Distinct interfaces can therefore
    # safely enforce distinct source policies for the same container port.
    target_policies: dict[tuple[str, int, str], tuple[str, ...]] = {}
    for entry in external:
        ingress = ingress_by_publication[
            (entry.service, entry.protocol, entry.host_address, entry.port)
        ]
        policy = tuple(sorted(ingress.sources))
        target = (entry.protocol, entry.target_port, ingress.interface)
        previous = target_policies.setdefault(target, policy)
        if previous != policy:
            raise FirewallError("published_target_ingress_ambiguous")

    forward_rules = [
        f'    iifname "{ingress.interface}" {_source_expression(source)} '
        f"ct original {_destination_expression(ingress.bind_address)} "
        f"{ingress.protocol} dport {entry.target_port} accept"
        for ingress in firewall.ingress
        for port in ingress.ports
        for entry in external
        if (entry.service, entry.protocol, entry.host_address, entry.port)
        == (ingress.service, ingress.protocol, ingress.bind_address, port)
        for source in ingress.sources
    ]
    forward_rules = list(dict.fromkeys(forward_rules))
    resolver_rules = [
        f'    iifname "{interface}" {protocol} dport 53 accept'
        for protocol in ("udp", "tcp")
        for interface in ("docker0", "br-*")
    ]
    if not {rule.service for rule in firewall.host_bridge_ingress}.issubset(host_networked):
        raise FirewallError("bridge_ingress_service_not_host_networked")
    bridge_rules = [
        f'    iifname "{interface}" {rule.protocol} dport {port} accept'
        for rule in firewall.host_bridge_ingress
        for port in rule.ports
        for interface in ("docker0", "br-*")
    ]
    ssh_interface = _interface_expression(firewall.management_ssh.interface)
    ssh_port = firewall.management_ssh.port
    ssh_rules = [
        f"    {ssh_interface}{_source_expression(source)} tcp dport {ssh_port} accept"
        for source in firewall.management_ssh.sources
    ]
    body = "\n".join(
        [
            "delete table inet infralink_filter",
            "table inet infralink_filter {",
            "  chain input {",
            "    type filter hook input priority filter; policy drop;",
            '    iifname "lo" accept',
            "    ct state established,related accept",
            # Tailscale's WireGuard listener is a host-runtime prerequisite,
            # not an application ingress declaration.
            "    udp dport 41641 accept",
            # Docker's embedded resolver reaches the host bridge gateway, then
            # forwards to the host's configured DNS upstream. This is a
            # controller runtime prerequisite, not service-specific egress.
            *resolver_rules,
            *bridge_rules,
            *ssh_rules,
            *ingress_rules,
            "  }",
            "  chain forward {",
            "    type filter hook forward priority filter; policy drop;",
            "    ct state established,related accept",
            # Docker bridge containers have ordinary outbound connectivity.
            # The firewall owns host ingress, not per-application egress
            # allowlists, which would make image maintenance brittle.
            '    iifname "docker0" accept',
            '    iifname "br-*" accept',
            *forward_rules,
            "  }",
            "}",
            "",
        ]
    )
    return body.encode("utf-8")


def verify_firewall_policy(
    *, firewall: FirewallPolicy, compose: bytes, runner: CommandRunner | None = None
) -> None:
    """Fail closed unless the owned runtime table contains every declared rule."""

    expected = render_firewall_policy(firewall=firewall, compose=compose)
    try:
        argv = ["nft", "list", "table", "inet", "infralink_filter"]
        actual = (
            subprocess.run(argv, check=False, text=True, capture_output=True)
            if runner is None
            else runner(argv)
        )
    except OSError as error:
        raise FirewallError("firewall_runtime_unavailable") from error
    if actual.returncode != 0 or not isinstance(actual.stdout, str):
        raise FirewallError("firewall_runtime_unavailable")
    required_base_chains = (
        "type filter hook input priority filter; policy drop;",
        "type filter hook forward priority filter; policy drop;",
    )
    if any(chain not in actual.stdout for chain in required_base_chains):
        raise FirewallError("firewall_runtime_drift")
    expected_rules = [
        line.strip()
        for line in expected.decode("utf-8").splitlines()
        if line.startswith("    ")
        and line.strip() not in {"}", "{"}
        and not line.strip().startswith(("type ", "policy ", "chain "))
    ]
    actual_policy = re.sub(r"\s+# handle \d+", "", actual.stdout)
    if any(rule not in actual_policy for rule in expected_rules):
        raise FirewallError("firewall_runtime_drift")
