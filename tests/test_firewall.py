from __future__ import annotations

from pathlib import Path

from infralink.firewall import FirewallPolicy


def _policy() -> FirewallPolicy:
    return FirewallPolicy.model_validate(
        {
            "backend": "nftables",
            "mode": "default-deny",
            "management_ssh": {
                "port": 22,
                "interface": "tailscale0",
                "sources": ["100.64.0.0/10"],
            },
            "ingress": [
                {
                    "service": "api",
                    "protocol": "tcp",
                    "ports": [8443],
                    "interface": "tailscale0",
                    "bind_address": "100.64.0.10",
                    "sources": ["100.64.0.0/10"],
                }
            ],
        }
    )


def test_render_firewall_policy_emits_declared_tailnet_ingress() -> None:
    from infralink_ops.firewall import render_firewall_policy

    rendered = render_firewall_policy(
        firewall=_policy(),
        compose=(
            b"services:\n"
            b"  api:\n"
            b"    image: example/api\n"
            b"    ports:\n"
            b"      - 100.64.0.10:8443:8443/tcp\n"
        ),
    )

    assert rendered == (
        b"destroy table inet infralink_filter\n"
        b"table inet infralink_filter {\n"
        b"  chain input {\n"
        b"    type filter hook input priority filter; policy drop;\n"
        b'    iifname "lo" accept\n'
        b"    ct state established,related accept\n"
        b"    udp dport 41641 accept\n"
        b'    iifname "docker0" udp dport 53 accept\n'
        b'    iifname "docker0" tcp dport 53 accept\n'
        b'    iifname "br-*" udp dport 53 accept\n'
        b'    iifname "br-*" tcp dport 53 accept\n'
        b'    iifname "tailscale0" ip saddr 100.64.0.0/10 tcp dport 22 accept\n'
        b'    iifname "tailscale0" tcp dport 22 accept\n'
        b'    iifname "tailscale0" ip saddr 100.64.0.0/10 tcp dport 8443 accept\n'
        b"  }\n"
        b"  chain forward {\n"
        b"    type filter hook forward priority filter; policy drop;\n"
        b"    ct state established,related accept\n"
        b'    iifname "docker0" accept\n'
        b'    iifname "br-*" accept\n'
        b'    iifname "tailscale0" ip saddr 100.64.0.0/10 tcp dport 8443 accept\n'
        b"  }\n"
        b"}\n"
    )


def test_load_firewall_policy_accepts_a_host_without_firewall_declaration(tmp_path: Path) -> None:
    from infralink_ops.firewall import load_firewall_policy

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("services: []\n", encoding="utf-8")

    assert load_firewall_policy(deployment) is None
