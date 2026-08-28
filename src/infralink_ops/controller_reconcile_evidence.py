"""Write typed controller reconciliation evidence and Prometheus metrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from infralink.controller_contracts import ControllerAdapterResult
from pydantic import ValidationError

from infralink_ops.controller_metrics import (
    MetricsError,
    atomic_write,
    render_failure,
    render_success,
)

SCHEMA_VERSION = "infralink.ops.controller-reconcile-evidence/v1"
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class EvidenceError(ValueError):
    """A typed controller-evidence request error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EnvelopeParser(argparse.ArgumentParser):
    """Keep usage failures within the runnable response contract."""

    def error(self, message: str) -> None:
        raise EvidenceError("usage_error")


def _json_mapping(value: str, error_code: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise EvidenceError(error_code) from error
    if not isinstance(parsed, dict):
        raise EvidenceError(error_code)
    return parsed


def _validate_common(args: argparse.Namespace) -> None:
    try:
        uuid.UUID(args.host_uuid)
    except ValueError as error:
        raise EvidenceError("host_uuid_invalid") from error
    if not args.runtime_root.is_dir():
        raise EvidenceError("runtime_directory_missing")
    if not args.textfile_directory.is_dir():
        raise EvidenceError("textfile_directory_missing")
    try:
        observed_at = datetime.fromisoformat(args.observed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceError("observed_at_invalid") from error
    if observed_at.tzinfo is None:
        raise EvidenceError("observed_at_invalid")


def _validate_success(args: argparse.Namespace) -> None:
    _validate_common(args)
    if _GIT_SHA.fullmatch(args.registry_revision) is None:
        raise EvidenceError("registry_revision_invalid")
    if not args.registry_ref or not args.registry_repo_url:
        raise EvidenceError("registry_source_invalid")
    if _DIGEST.fullmatch(args.controller_digest) is None:
        raise EvidenceError("controller_digest_invalid")
    if not args.controller_reference.endswith(f"@{args.controller_digest}"):
        raise EvidenceError("controller_reference_invalid")


def _validated_adapter(value: str, registry_revision: str) -> dict[str, Any]:
    try:
        result = ControllerAdapterResult.model_validate(_json_mapping(value, "adapter_invalid"))
    except ValidationError as error:
        raise EvidenceError("adapter_invalid") from error
    if result.phase != "apply" or result.status != "applied":
        raise EvidenceError("adapter_invalid")
    if result.registry_revision != registry_revision:
        raise EvidenceError("adapter_invalid")
    return result.model_dump(mode="json")


def _validated_image_cache(value: str) -> dict[str, str]:
    cache = _json_mapping(value, "controller_image_cache_invalid")
    if set(cache) - {"status", "reason"}:
        raise EvidenceError("controller_image_cache_invalid")
    status = cache.get("status")
    reason = cache.get("reason")
    if status not in {"ok", "warning"}:
        raise EvidenceError("controller_image_cache_invalid")
    if reason is not None and (
        not isinstance(reason, str) or _REASON_CODE.fullmatch(reason) is None
    ):
        raise EvidenceError("controller_image_cache_invalid")
    if status == "ok" and reason is not None:
        raise EvidenceError("controller_image_cache_invalid")
    result = {"status": status}
    if reason is not None:
        result["reason"] = reason
    return result


def _validated_failure_details(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    details = _json_mapping(value, "failure_details_invalid")
    if set(details) != {"stage", "exit_code", "diagnostic_code"}:
        raise EvidenceError("failure_details_invalid")
    if details["stage"] != "adapter":
        raise EvidenceError("failure_details_invalid")
    if (
        not isinstance(details["exit_code"], int)
        or isinstance(details["exit_code"], bool)
        or not 1 <= details["exit_code"] <= 255
    ):
        raise EvidenceError("failure_details_invalid")
    if (
        not isinstance(details["diagnostic_code"], str)
        or _REASON_CODE.fullmatch(details["diagnostic_code"]) is None
    ):
        raise EvidenceError("failure_details_invalid")
    return details


def _payload(
    command: str | None, *, result: dict[str, Any] | None = None, error: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command is not None else [], "args": {}},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = {"code": error}
    return payload


def write_success(args: argparse.Namespace) -> dict[str, Any]:
    _validate_success(args)
    adapter = _validated_adapter(args.adapter_json, args.registry_revision)
    image_cache = _validated_image_cache(args.controller_image_cache_json)
    record = {
        "schema_version": "infralink.controller-reconcile/v2",
        "status": "success",
        "host_uuid": args.host_uuid,
        "registry_head": args.registry_revision,
        "registry_ref": args.registry_ref,
        "registry_repo_url": args.registry_repo_url,
        "controller_reference": args.controller_reference,
        "controller_digest": args.controller_digest,
        "controller_image_cache": image_cache,
        "adapter": adapter,
        "observed_at": args.observed_at,
    }
    evidence_path = args.runtime_root / "reconcile-result.yml"
    metrics_path = args.textfile_directory / "infralink-controller-reconcile.prom"
    try:
        metrics = render_success(args.registry_revision, args.observed_at)
        atomic_write(evidence_path, yaml.safe_dump(record, sort_keys=False).encode("utf-8"))
        atomic_write(metrics_path, metrics)
    except MetricsError as error:
        raise EvidenceError(error.code) from error
    return {
        "status": "success",
        "evidence_path": str(evidence_path),
        "metrics_path": str(metrics_path),
    }


def write_failure(args: argparse.Namespace) -> dict[str, Any]:
    _validate_common(args)
    if _REASON_CODE.fullmatch(args.reason_code) is None:
        raise EvidenceError("reason_code_invalid")
    failure_details = _validated_failure_details(args.failure_details_json)
    record = {
        "schema_version": "infralink.controller-reconcile/v2",
        "status": "failure",
        "host_uuid": args.host_uuid,
        "reason_code": args.reason_code,
        "observed_at": args.observed_at,
    }
    if failure_details is not None:
        record["failure"] = failure_details
    evidence_path = args.runtime_root / "reconcile-result.yml"
    metrics_path = args.textfile_directory / "infralink-controller-reconcile.prom"
    try:
        atomic_write(evidence_path, yaml.safe_dump(record, sort_keys=False).encode("utf-8"))
        atomic_write(metrics_path, render_failure())
    except MetricsError as error:
        raise EvidenceError(error.code) from error
    return {
        "status": "failure",
        "evidence_path": str(evidence_path),
        "metrics_path": str(metrics_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = EnvelopeParser(prog="infralink-controller-reconcile-evidence")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=EnvelopeParser)
    success = commands.add_parser("write-success")
    success.add_argument("--runtime-root", required=True, type=Path)
    success.add_argument("--textfile-directory", required=True, type=Path)
    success.add_argument("--host-uuid", required=True)
    success.add_argument("--registry-revision", required=True)
    success.add_argument("--registry-ref", required=True)
    success.add_argument("--registry-repo-url", required=True)
    success.add_argument("--controller-reference", required=True)
    success.add_argument("--controller-digest", required=True)
    success.add_argument("--adapter-json", required=True)
    success.add_argument("--observed-at", required=True)
    success.add_argument("--controller-image-cache-json", required=True)
    failure = commands.add_parser("write-failure")
    failure.add_argument("--runtime-root", required=True, type=Path)
    failure.add_argument("--textfile-directory", required=True, type=Path)
    failure.add_argument("--host-uuid", required=True)
    failure.add_argument("--reason-code", required=True)
    failure.add_argument("--failure-details-json")
    failure.add_argument("--observed-at", required=True)
    try:
        args = parser.parse_args(argv)
        result = write_success(args) if args.command == "write-success" else write_failure(args)
    except EvidenceError as error:
        sys.stdout.write(yaml.safe_dump(_payload(None, error=error.code)))
        return 64 if error.code.endswith("_invalid") or error.code == "usage_error" else 78
    sys.stdout.write(yaml.safe_dump(_payload(args.command, result=result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
