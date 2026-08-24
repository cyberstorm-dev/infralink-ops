from __future__ import annotations

import pytest

from infralink_ops.egress_snat import EgressSnatError, EgressSnatRule, reconcile_egress_snat


def _rule() -> EgressSnatRule:
    return EgressSnatRule(
        source_cidr="172.21.0.0/16",
        protocol="tcp",
        ports=(25, 587),
        to_source="5.161.26.199",
    )


def test_reconcile_installs_one_owned_chain_before_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    import infralink_ops.egress_snat as module

    calls: list[list[str]] = []
    restore_bodies: list[bytes] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append(argv)
        if argv == ["/usr/sbin/iptables-restore", "--noflush"]:
            restore_bodies.append(kwargs["input"])  # type: ignore[index]
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    reconcile_egress_snat((_rule(),))

    assert [
        "/usr/sbin/iptables",
        "-t",
        "nat",
        "-I",
        "POSTROUTING",
        "1",
        "-j",
        "INFRALINK_EGRESS_SNAT",
    ] in calls
    assert restore_bodies == [
        b"*nat\n"
        b"-F INFRALINK_EGRESS_SNAT\n"
        b"-A INFRALINK_EGRESS_SNAT -s 172.21.0.0/16 -p tcp -m tcp --dport 25 -j SNAT "
        b"--to-source 5.161.26.199\n"
        b"-A INFRALINK_EGRESS_SNAT -s 172.21.0.0/16 -p tcp -m tcp --dport 587 -j SNAT "
        b"--to-source 5.161.26.199\n"
        b"COMMIT\n"
    ]


def test_reconcile_removes_only_owned_chain_when_undeclared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import infralink_ops.egress_snat as module

    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> object:
        calls.append(argv)
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    reconcile_egress_snat(())

    assert ["/usr/sbin/iptables", "-t", "nat", "-F", "INFRALINK_EGRESS_SNAT"] in calls
    assert ["/usr/sbin/iptables", "-t", "nat", "-X", "INFRALINK_EGRESS_SNAT"] in calls


def test_failed_new_restore_reinstates_prior_owned_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    import infralink_ops.egress_snat as module

    restore_bodies: list[bytes] = []
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append(argv)
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "INFRALINK_EGRESS_SNAT"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "-N INFRALINK_EGRESS_SNAT\n"
                    "-A INFRALINK_EGRESS_SNAT -s 172.21.0.0/16 -p tcp -m tcp --dport 25 "
                    "-j SNAT --to-source 5.161.17.242\n",
                },
            )()
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "-N POSTROUTING\n-A POSTROUTING -j DOCKER\n"
                    "-A POSTROUTING -j INFRALINK_EGRESS_SNAT\n",
                },
            )()
        if argv == ["/usr/sbin/iptables-restore", "--noflush"]:
            restore_bodies.append(kwargs["input"])  # type: ignore[index]
            return type("Result", (), {"returncode": int(len(restore_bodies) == 1)})()
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    with pytest.raises(EgressSnatError):
        reconcile_egress_snat((_rule(),))

    assert len(restore_bodies) == 2
    assert b"--to-source 5.161.17.242" in restore_bodies[1]
    assert [
        "/usr/sbin/iptables",
        "-t",
        "nat",
        "-I",
        "POSTROUTING",
        "2",
        "-j",
        "INFRALINK_EGRESS_SNAT",
    ] in calls


def test_failed_new_restore_reinstates_empty_prior_owned_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import infralink_ops.egress_snat as module

    restore_bodies: list[bytes] = []
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append(argv)
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "INFRALINK_EGRESS_SNAT"]:
            return type("Result", (), {"returncode": 0, "stdout": "-N INFRALINK_EGRESS_SNAT\n"})()
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "-N POSTROUTING\n-A POSTROUTING -j DOCKER\n"
                    "-A POSTROUTING -j INFRALINK_EGRESS_SNAT\n",
                },
            )()
        if argv == ["/usr/sbin/iptables-restore", "--noflush"]:
            restore_bodies.append(kwargs["input"])  # type: ignore[index]
            return type("Result", (), {"returncode": int(len(restore_bodies) == 1)})()
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    with pytest.raises(EgressSnatError):
        reconcile_egress_snat((_rule(),))

    assert restore_bodies[1] == b"*nat\n-F INFRALINK_EGRESS_SNAT\nCOMMIT\n"
    assert [
        "/usr/sbin/iptables",
        "-t",
        "nat",
        "-I",
        "POSTROUTING",
        "2",
        "-j",
        "INFRALINK_EGRESS_SNAT",
    ] in calls


def test_unknown_existing_chain_rule_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import infralink_ops.egress_snat as module

    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> object:
        calls.append(argv)
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "INFRALINK_EGRESS_SNAT"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "-N INFRALINK_EGRESS_SNAT\n"
                    "-A INFRALINK_EGRESS_SNAT -m comment --comment 'unexpected rule' -j ACCEPT\n",
                },
            )()
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"]:
            return type("Result", (), {"returncode": 0, "stdout": "-N POSTROUTING\n"})()
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    with pytest.raises(EgressSnatError):
        reconcile_egress_snat((_rule(),))

    assert calls == [
        ["/usr/sbin/iptables", "-t", "nat", "-S", "INFRALINK_EGRESS_SNAT"],
        ["/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"],
    ]


@pytest.mark.parametrize("protocol", ("tcp", "udp"))
def test_reconcile_accepts_iptables_canonical_owned_rule(
    monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    import infralink_ops.egress_snat as module

    def run(argv: list[str], **_kwargs: object) -> object:
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "INFRALINK_EGRESS_SNAT"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "-N INFRALINK_EGRESS_SNAT\n"
                    f"-A INFRALINK_EGRESS_SNAT -s 172.21.0.0/16 -p {protocol} -m {protocol} "
                    "--dport 25 -j SNAT --to-source 5.161.17.242\n",
                },
            )()
        if argv == ["/usr/sbin/iptables", "-t", "nat", "-S", "POSTROUTING"]:
            return type("Result", (), {"returncode": 0, "stdout": "-N POSTROUTING\n"})()
        if argv[:5] == ["/usr/sbin/iptables", "-t", "nat", "-D", "POSTROUTING"]:
            return type("Result", (), {"returncode": 1})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    monkeypatch.setattr(module.subprocess, "run", run)

    reconcile_egress_snat(
        (
            EgressSnatRule(
                source_cidr="172.21.0.0/16",
                protocol=protocol,
                ports=(25,),
                to_source="5.161.26.199",
            ),
        )
    )


@pytest.mark.parametrize(
    "rule",
    (
        EgressSnatRule("not-a-cidr", "tcp", (25,), "5.161.26.199"),
        EgressSnatRule("172.21.0.0/16", "icmp", (25,), "5.161.26.199"),
        EgressSnatRule("172.21.0.0/16", "tcp", (0,), "5.161.26.199"),
        EgressSnatRule("172.21.0.0/16", "tcp", (25,), "not-an-ip"),
        EgressSnatRule("::/0", "tcp", (25,), "5.161.26.199"),
        EgressSnatRule("172.21.0.0/16", "tcp", (25,), "127.0.0.1"),
    ),
)
def test_reconcile_rejects_invalid_rule_before_iptables(
    monkeypatch: pytest.MonkeyPatch, rule: EgressSnatRule
) -> None:
    import infralink_ops.egress_snat as module

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("iptables must not run"),
    )

    with pytest.raises(EgressSnatError):
        reconcile_egress_snat((rule,))
