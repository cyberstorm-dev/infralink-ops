"""Controller-owned iptables SNAT chain reconciliation."""

from __future__ import annotations

import ipaddress
import shlex
import subprocess
from dataclasses import dataclass

_IPTABLES = "/usr/sbin/iptables"
_IPTABLES_RESTORE = "/usr/sbin/iptables-restore"
_CHAIN = "INFRALINK_EGRESS_SNAT"
_MAX_JUMPS = 128


class EgressSnatError(RuntimeError):
    """The controller-owned egress SNAT chain could not be reconciled."""


@dataclass(frozen=True, slots=True)
class EgressSnatRule:
    """One source-restricted TCP or UDP SNAT rule."""

    source_cidr: str
    protocol: str
    ports: tuple[int, ...]
    to_source: str


@dataclass(frozen=True, slots=True)
class EgressSnatSnapshot:
    """Typed transient state for compensating an owned SNAT-chain operation."""

    chain_exists: bool
    chain_rules: tuple[EgressSnatRule, ...]
    jump_positions: tuple[int, ...]


def _validate_rule(rule: EgressSnatRule) -> EgressSnatRule:
    if type(rule) is not EgressSnatRule:
        raise EgressSnatError("egress SNAT rule is invalid")
    if type(rule.source_cidr) is not str or type(rule.to_source) is not str:
        raise EgressSnatError("egress SNAT rule is invalid")
    try:
        network = ipaddress.ip_network(rule.source_cidr, strict=True)
        address = ipaddress.ip_address(rule.to_source)
    except ValueError:
        raise EgressSnatError("egress SNAT rule is invalid") from None
    if (
        network.version != 4
        or network.prefixlen == 0
        or str(network) != rule.source_cidr
        or address.version != 4
        or address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address.is_link_local
        or str(address) != rule.to_source
    ):
        raise EgressSnatError("egress SNAT rule is invalid")
    if rule.protocol not in {"tcp", "udp"} or type(rule.ports) is not tuple:
        raise EgressSnatError("egress SNAT rule is invalid")
    if not rule.ports or len(rule.ports) > 64 or len(set(rule.ports)) != len(rule.ports):
        raise EgressSnatError("egress SNAT rule is invalid")
    if any(type(port) is not int or not 1 <= port <= 65535 for port in rule.ports):
        raise EgressSnatError("egress SNAT rule is invalid")
    return rule


def _validate_rules(rules: tuple[EgressSnatRule, ...]) -> tuple[EgressSnatRule, ...]:
    if type(rules) is not tuple or len(rules) > 128:
        raise EgressSnatError("egress SNAT rules are invalid")
    return tuple(_validate_rule(rule) for rule in rules)


def _run(*arguments: str) -> int:
    return subprocess.run([_IPTABLES, "-t", "nat", *arguments], check=False).returncode


def _rules(chain: str) -> tuple[tuple[str, ...], ...] | None:
    result = subprocess.run(
        [_IPTABLES, "-t", "nat", "-S", chain],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode == 1:
        return None
    if result.returncode != 0 or type(result.stdout) is not str:
        raise EgressSnatError("cannot inspect egress SNAT chain")
    try:
        return tuple(tuple(shlex.split(line)) for line in result.stdout.splitlines())
    except ValueError:
        raise EgressSnatError("cannot inspect egress SNAT chain") from None


def _remove_jumps() -> None:
    jump = ("-j", _CHAIN)
    for _ in range(_MAX_JUMPS):
        if _run("-D", "POSTROUTING", *jump) != 0:
            return
    raise EgressSnatError("too many controller-owned egress SNAT jumps")


def _remove_chain() -> None:
    _remove_jumps()
    if _run("-F", _CHAIN) not in {0, 1} or _run("-X", _CHAIN) not in {0, 1}:
        raise EgressSnatError("cannot remove controller-owned egress SNAT chain")


def _parse_owned_rule(tokens: tuple[str, ...]) -> EgressSnatRule:
    if (
        len(tokens) != 14
        or tokens[:3] != ("-A", _CHAIN, "-s")
        or tokens[4] != "-p"
        or tokens[6] != "-m"
        or tokens[7] != tokens[5]
        or tokens[8] != "--dport"
        or tokens[10:13] != ("-j", "SNAT", "--to-source")
    ):
        raise EgressSnatError("cannot inspect egress SNAT chain")
    try:
        port = int(tokens[9])
    except ValueError:
        raise EgressSnatError("cannot inspect egress SNAT chain") from None
    if str(port) != tokens[9]:
        raise EgressSnatError("cannot inspect egress SNAT chain")
    return _validate_rule(EgressSnatRule(tokens[3], tokens[5], (port,), tokens[13]))


def capture_egress_snat() -> EgressSnatSnapshot:
    """Capture only canonical state owned by the controller SNAT chain."""
    chain = _rules(_CHAIN)
    postrouting = _rules("POSTROUTING")
    if postrouting is None:
        raise EgressSnatError("cannot inspect egress SNAT chain")
    chain_rules: list[EgressSnatRule] = []
    for rule in chain or ():
        if rule == ("-N", _CHAIN):
            continue
        chain_rules.append(_parse_owned_rule(rule))
    postrouting_rules = tuple(rule for rule in postrouting if rule[:2] == ("-A", "POSTROUTING"))
    jump = ("-A", "POSTROUTING", "-j", _CHAIN)
    return EgressSnatSnapshot(
        chain_exists=chain is not None,
        chain_rules=tuple(chain_rules),
        jump_positions=tuple(
            index for index, rule in enumerate(postrouting_rules, start=1) if rule == jump
        ),
    )


def restore_egress_snat(snapshot: EgressSnatSnapshot) -> None:
    """Restore a transient snapshot of the controller-owned SNAT chain."""
    if type(snapshot) is not EgressSnatSnapshot:
        raise EgressSnatError("egress SNAT snapshot is invalid")
    _remove_chain()
    if not snapshot.chain_exists:
        return
    if _run("-N", _CHAIN) != 0:
        raise EgressSnatError("cannot restore egress SNAT chain")
    body = _render(snapshot.chain_rules)
    if subprocess.run([_IPTABLES_RESTORE, "--noflush"], input=body, check=False).returncode != 0:
        raise EgressSnatError("cannot restore egress SNAT chain")
    for position in reversed(snapshot.jump_positions):
        if _run("-I", "POSTROUTING", str(position), "-j", _CHAIN) != 0:
            raise EgressSnatError("cannot restore egress SNAT chain")


def _render(rules: tuple[EgressSnatRule, ...]) -> bytes:
    lines = ["*nat", f"-F {_CHAIN}"]
    for rule in rules:
        for port in rule.ports:
            lines.append(
                " ".join(
                    (
                        "-A",
                        _CHAIN,
                        "-s",
                        rule.source_cidr,
                        "-p",
                        rule.protocol,
                        "-m",
                        rule.protocol,
                        "--dport",
                        str(port),
                        "-j",
                        "SNAT",
                        "--to-source",
                        rule.to_source,
                    )
                )
            )
    lines.append("COMMIT")
    return ("\n".join(lines) + "\n").encode("ascii")


def reconcile_egress_snat(rules: tuple[EgressSnatRule, ...]) -> None:
    """Atomically replace the controller-owned SNAT chain before Docker NAT rules."""
    validated = _validate_rules(rules)
    snapshot = capture_egress_snat()
    try:
        if not validated:
            _remove_chain()
            return
        if _run("-N", _CHAIN) not in {0, 1}:
            raise EgressSnatError("cannot create egress SNAT chain")
        if (
            subprocess.run(
                [_IPTABLES_RESTORE, "--noflush"], input=_render(validated), check=False
            ).returncode
            != 0
        ):
            raise EgressSnatError("cannot apply egress SNAT chain")
        _remove_jumps()
        if _run("-I", "POSTROUTING", "1", "-j", _CHAIN) != 0:
            raise EgressSnatError("cannot install egress SNAT jump")
    except (OSError, subprocess.SubprocessError, EgressSnatError):
        try:
            restore_egress_snat(snapshot)
        except (OSError, subprocess.SubprocessError, EgressSnatError):
            pass
        raise EgressSnatError("egress SNAT reconciliation failed") from None
