"""One private, bounded host-interface transition for legacy controller handoffs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from infralink_ops import controller_host_interface as host_interface

SCHEMA_VERSION = "infralink.ops.controller-host-transition/v1"


class HostTransitionError(ValueError):
    """The private controller transition cannot safely mutate its fixed host interface."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid internal invocation machine-readable for its caller."""

    def error(self, message: str) -> None:
        raise HostTransitionError("usage_error")


def _payload(*, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "ok": error is None}
    if error is None:
        payload["result"] = result or {}
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Refresh the private interface, then derive the immutable runtime seed."""

    parser = EnvelopeParser(prog="infralink-ops-controller-host-transition")
    parser.add_argument("command", choices=("transition",))
    parser.add_argument("--host-root", required=True, type=Path)
    parser.add_argument("--controller-reference", required=True)
    try:
        arguments = parser.parse_args(argv)
        host_root = host_interface._host_root(arguments.host_root)
        interface = host_interface.refresh(host_root)
        seed = host_interface.transition_controller_seed(host_root, arguments.controller_reference)
    except (HostTransitionError, host_interface.HostInterfaceError) as error:
        return _payload(
            error="controller_seed_transition_failed"
            if str(error) != "usage_error"
            else "usage_error"
        ), 78
    return _payload(result={"host_interface": interface, "seed": seed}), 0


if __name__ == "__main__":
    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    raise SystemExit(status)
