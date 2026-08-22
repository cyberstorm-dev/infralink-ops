"""Publish controller convergence evidence for a node-exporter textfile collector."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "infralink.ops.controller-metrics/v1"
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


class MetricsError(ValueError):
    """A typed controller-metrics request error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EnvelopeParser(argparse.ArgumentParser):
    """Keep command-usage failures within the runnable's response contract."""

    def error(self, message: str) -> None:
        raise MetricsError("usage_error")


def _timestamp(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MetricsError("observed_at_invalid") from error
    if parsed.tzinfo is None:
        raise MetricsError("observed_at_invalid")
    return int(parsed.timestamp())


def render_success(registry_revision: str, observed_at: str) -> bytes:
    if _GIT_SHA.fullmatch(registry_revision) is None:
        raise MetricsError("registry_revision_invalid")
    timestamp = _timestamp(observed_at)
    return (
        "# HELP infralink_controller_reconcile_converged Whether the latest Infralink "
        "controller reconciliation converged.\n"
        "# TYPE infralink_controller_reconcile_converged gauge\n"
        "infralink_controller_reconcile_converged 1\n"
        "# HELP infralink_controller_reconcile_last_success_timestamp_seconds Completion "
        "time of the latest successful controller reconciliation.\n"
        "# TYPE infralink_controller_reconcile_last_success_timestamp_seconds gauge\n"
        f"infralink_controller_reconcile_last_success_timestamp_seconds {timestamp}\n"
        "# HELP infralink_controller_reconcile_registry_revision_info Registry revision "
        "applied by the latest successful controller reconciliation.\n"
        "# TYPE infralink_controller_reconcile_registry_revision_info gauge\n"
        "infralink_controller_reconcile_registry_revision_info"
        f'{{revision="{registry_revision}"}} 1\n'
    ).encode("ascii")


def render_failure() -> bytes:
    return (
        "# HELP infralink_controller_reconcile_converged Whether the latest Infralink "
        "controller reconciliation converged.\n"
        "# TYPE infralink_controller_reconcile_converged gauge\n"
        "infralink_controller_reconcile_converged 0\n"
    ).encode("ascii")


def atomic_write(output: Path, body: bytes) -> None:
    if not output.parent.is_dir():
        raise MetricsError("output_directory_missing")
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as staged:
            staged_path = Path(staged.name)
            staged.write(body)
            staged.flush()
            os.fsync(staged.fileno())
        os.chmod(staged_path, 0o644)
        staged_path.replace(output)
    except OSError as error:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise MetricsError("output_write_failed") from error


def _payload(
    *,
    command: str | None,
    output: Path | None,
    status: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {
            "path": [command] if command is not None else [],
            "args": {"output": str(output)} if output is not None else {},
        },
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = {"output": str(output), "status": status}
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = EnvelopeParser(prog="infralink-controller-metrics")
    commands = parser.add_subparsers(dest="command", required=True)
    success = commands.add_parser("publish-success")
    success.add_argument("--output", required=True, type=Path)
    success.add_argument("--registry-revision", required=True)
    success.add_argument("--observed-at", required=True)
    failure = commands.add_parser("publish-failure")
    failure.add_argument("--output", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except MetricsError as error:
        sys.stdout.write(yaml.safe_dump(_payload(command=None, output=None, error=error.code)))
        return 64

    try:
        body = (
            render_success(args.registry_revision, args.observed_at)
            if args.command == "publish-success"
            else render_failure()
        )
        atomic_write(args.output, body)
    except MetricsError as error:
        sys.stdout.write(
            yaml.safe_dump(_payload(command=args.command, output=args.output, error=error.code))
        )
        return 64 if error.code.endswith("_invalid") else 78

    status = "success" if args.command == "publish-success" else "failure"
    sys.stdout.write(
        yaml.safe_dump(_payload(command=args.command, output=args.output, status=status))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
