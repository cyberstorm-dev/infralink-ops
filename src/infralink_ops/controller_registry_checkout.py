"""Fetch one declared registry revision for a controller runtime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from infralink_ops.registry_checkout import RegistryCheckoutError, fetch_configured_registry

SCHEMA_VERSION = "infralink.ops.registry-checkout/v1"


class EnvelopeParser(argparse.ArgumentParser):
    """Keep usage failures in the runnable's machine-readable contract."""

    def error(self, message: str) -> None:
        raise RegistryCheckoutError("usage_error")


def _payload(
    *,
    command: str | None,
    registry_root: Path | None,
    revision: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {
            "path": [command] if command is not None else [],
            "args": {"registry_root": str(registry_root)} if registry_root is not None else {},
        },
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = {
            "registry_root": str(registry_root.resolve()),
            "revision": revision,
        }
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run strict configured-registry checkout with a bounded YAML envelope."""

    parser = EnvelopeParser(prog="infralink-controller-registry-checkout")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--registry-root", required=True, type=Path)
    fetch.add_argument("--remote", required=True)
    fetch.add_argument("--ref", required=True)
    fetch.add_argument("--identity-file", required=True, type=Path)
    fetch.add_argument("--known-hosts-file", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except RegistryCheckoutError:
        sys.stdout.write(
            yaml.safe_dump(_payload(command=None, registry_root=None, error="usage_error"))
        )
        return 64

    try:
        checkout = fetch_configured_registry(
            args.registry_root,
            configured_remote=args.remote,
            configured_ref=args.ref,
            identity_file=args.identity_file,
            known_hosts_file=args.known_hosts_file,
        )
    except RegistryCheckoutError:
        sys.stdout.write(
            yaml.safe_dump(
                _payload(
                    command=args.command,
                    registry_root=args.registry_root,
                    error="registry_checkout_failed",
                )
            )
        )
        return 78

    sys.stdout.write(
        yaml.safe_dump(
            _payload(
                command=args.command,
                registry_root=checkout.root,
                revision=checkout.revision,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
