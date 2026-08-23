"""Read-only local health evidence for a registry-driven host controller."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from infralink_ops.firewall import FirewallError, load_firewall_policy, verify_firewall_policy
from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision

SCHEMA_VERSION = "infralink.controller-doctor/v1"


class DoctorError(ValueError):
    """Local controller evidence is incomplete or inconsistent."""


class ComposeConfigError(DoctorError):
    """The rendered Compose declaration cannot be read."""


class ComposeStateError(DoctorError):
    """The live Compose service state cannot be read."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid invocation in the typed response."""

    def error(self, message: str) -> None:
        raise DoctorError("usage_error")


def _host_environment(path: Path) -> dict[str, str]:
    try:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, raw_value = line.partition("=")
            if not separator or not key.isidentifier():
                raise DoctorError("host_environment_invalid")
            parts = shlex.split(raw_value, posix=True)
            if len(parts) != 1:
                raise DoctorError("host_environment_invalid")
            values[key] = parts[0]
        return values
    except (OSError, ValueError) as error:
        raise DoctorError("host_environment_invalid") from error


def _payload(
    *, status: str, reason: str, environment: dict[str, str], registry: Path, head: str | None
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "evidence": {
            "host_uuid": environment.get("INFRALINK_HOST_UUID"),
            "controller_image": environment.get("INFRALINK_CONTROLLER_IMAGE"),
            "controller": {"reference": None, "digest": None},
            "registry": {
                "path": str(registry),
                "ref": environment.get("INFRALINK_REGISTRY_REF"),
                "head": head,
            },
        },
    }


def _result(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise DoctorError("controller_reconcile_evidence_stale") from error
    if not isinstance(value, dict):
        raise DoctorError("controller_reconcile_evidence_stale")
    return value


def _run(docker: str, *arguments: str, failure: type[DoctorError]) -> str:
    try:
        result = subprocess.run(
            [docker, "compose", "-f", *arguments], text=True, capture_output=True, check=False
        )
    except OSError as error:
        raise failure("compose_command_failed") from error
    if result.returncode:
        raise failure("compose_command_failed")
    return result.stdout


def _compose_healthy(*, docker: str, compose: Path) -> bool:
    document = json.loads(
        _run(
            docker,
            str(compose),
            "config",
            "--format",
            "json",
            failure=ComposeConfigError,
        )
    )
    raw_state = _run(
        docker,
        str(compose),
        "ps",
        "--all",
        "--format",
        "json",
        failure=ComposeStateError,
    ).strip()
    state_items = (
        json.loads(raw_state)
        if raw_state.startswith("[")
        else [json.loads(line) for line in raw_state.splitlines() if line]
    )
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict) or not isinstance(state_items, list):
        raise DoctorError("declared_compose_unavailable")
    observed = {
        item.get("Service"): item
        for item in state_items
        if isinstance(item, dict) and isinstance(item.get("Service"), str)
    }
    for name, declaration in services.items():
        if not isinstance(declaration, dict):
            return False
        state = observed.get(name, {})
        if declaration.get("restart") == "no":
            if state.get("State") != "exited" or state.get("ExitCode") != 0:
                return False
        elif state.get("State") != "running":
            return False
    return True


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Collect local controller evidence without selecting or mutating state."""

    parser = EnvelopeParser(prog="infralink-controller-doctor")
    parser.add_argument("--host-env", type=Path, default=Path("/etc/infralink/host.env"))
    parser.add_argument("--registry", type=Path, default=Path("/var/lib/infralink/registry"))
    parser.add_argument("--registry-key", type=Path, default=Path("/etc/infralink/registry-read"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("/var/lib/infralink"))
    parser.add_argument("--services-dir", type=Path, default=Path("/opt/services"))
    parser.add_argument(
        "--textfile-directory", type=Path, default=Path("/var/lib/node_exporter/textfile_collector")
    )
    parser.add_argument("--docker", default="docker")
    try:
        args = parser.parse_args(argv)
        environment = _host_environment(args.host_env)
    except DoctorError as error:
        return (
            _payload(
                status="unhealthy",
                reason=str(error),
                environment={},
                registry=Path(""),
                head=None,
            ),
            64,
        )

    payload = _payload(
        status="unhealthy",
        reason="unknown",
        environment=environment,
        registry=args.registry,
        head=None,
    )
    host_id = environment.get("INFRALINK_HOST_UUID")
    if not environment.get("INFRALINK_CONTROLLER_IMAGE"):
        payload["reason"] = "controller_image_invalid"
        return payload, 78
    if not args.registry.is_dir() or not (args.registry / ".git").exists():
        payload["reason"] = "registry_checkout_missing"
        return payload, 78
    if not args.registry_key.is_file():
        payload["reason"] = "registry_key_missing"
        return payload, 78
    if not host_id or not (args.registry / "hosts" / host_id / "manifest.yml").is_file():
        payload["reason"] = "host_manifest_missing"
        return payload, 78
    evidence_path = args.runtime_dir / "reconcile-result.yml"
    if not evidence_path.is_file():
        payload["status"] = "unknown"
        payload["reason"] = "controller_reconcile_evidence_missing"
        return payload, 3
    evidence = _result(evidence_path)
    head = evidence.get("registry_head")
    if not isinstance(head, str):
        payload["reason"] = "controller_reconcile_evidence_stale"
        return payload, 78
    try:
        checkout = verify_registry_revision(args.registry, expected_revision=head)
    except RegistryCheckoutError:
        payload["reason"] = "controller_reconcile_evidence_stale"
        return payload, 78
    payload = _payload(
        status="unhealthy", reason="controller_reconcile_evidence_stale", environment=environment,
        registry=checkout.root, head=checkout.revision
    )
    if (
        evidence.get("status") != "success"
        or evidence.get("host_uuid") != host_id
        or evidence.get("registry_ref") != environment.get("INFRALINK_REGISTRY_REF")
        or evidence.get("registry_repo_url") != environment.get("INFRALINK_REGISTRY_REPO_URL")
        or not isinstance(evidence.get("controller_reference"), str)
        or not isinstance(evidence.get("controller_digest"), str)
        or "@sha256:" not in evidence["controller_digest"]
    ):
        return payload, 78
    payload["evidence"]["controller"] = {
        "reference": evidence["controller_reference"],
        "digest": evidence["controller_digest"],
    }
    compose = args.services_dir / "docker-compose.yml"
    if not compose.is_file():
        payload["reason"] = "rendered_compose_missing"
        return payload, 78
    try:
        compose_healthy = _compose_healthy(docker=args.docker, compose=compose)
    except ComposeConfigError:
        payload["reason"] = "declared_compose_unavailable"
        return payload, 78
    except ComposeStateError:
        payload["reason"] = "live_compose_unavailable"
        return payload, 78
    except (DoctorError, json.JSONDecodeError):
        payload["reason"] = "declared_compose_services_not_running"
        return payload, 78
    if not compose_healthy:
        payload["reason"] = "declared_compose_services_not_running"
        return payload, 78
    try:
        deployment = checkout.root / "hosts" / host_id / "operations" / "deployment.yml"
        if deployment.is_file():
            firewall = load_firewall_policy(deployment)
            if firewall is not None:
                verify_firewall_policy(firewall=firewall, compose=compose.read_bytes())
    except (FirewallError, OSError, UnicodeDecodeError):
        payload["reason"] = "declared_firewall_runtime_drift"
        return payload, 78
    metric = args.textfile_directory / "infralink-controller-reconcile.prom"
    if not args.textfile_directory.is_dir():
        payload["reason"] = "node_exporter_textfile_directory_missing"
        return payload, 78
    try:
        metrics = metric.read_text(encoding="utf-8")
    except OSError:
        metrics = ""
    metric_lines = set(metrics.splitlines())
    if (
        "infralink_controller_reconcile_converged 1" not in metric_lines
        or f'revision="{head}"' not in metrics
    ):
        payload["reason"] = "controller_reconcile_metric_stale"
        return payload, 78
    payload["status"] = "healthy"
    payload["reason"] = "controller_reconcile_evidence_present"
    return payload, 0


def cli() -> int:
    """Write the local controller doctor envelope as YAML."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
