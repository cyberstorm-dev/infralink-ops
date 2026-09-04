"""Private entrypoint for the controller image reconciliation lifecycle.

This module is intentionally not a console-script entry point.  The host
launcher invokes it through the controller image command, while public
operator work remains on the canonical ``infralink`` surface.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from infralink_ops import (
    controller_artifacts,
    controller_bootstrap,
    controller_config_consumers,
    controller_doctor,
    controller_firewall,
    controller_host_interface,
    controller_images,
    controller_protected_transitions,
    controller_reconcile_evidence,
    controller_render_secrets,
    controller_runtime_directories,
    template_renderer,
    typed_artifact_materializer,
)
from infralink_ops.image_resolution import (
    ImageResolutionError,
    resolve_controller_reference,
    resolve_host_images,
)
from infralink_ops.registry_checkout import (
    RegistryCheckoutError,
    fetch_configured_registry,
    verify_registry_revision,
)

SCHEMA_VERSION = "infralink.ops.controller-runtime/v1"
REGISTRY_ROOT = Path("/var/lib/infralink/registry")
RUNTIME_ROOT = Path("/var/lib/infralink")
SERVICES_ROOT = Path("/opt/services")
REGISTRY_KEY = Path("/etc/infralink/registry-read")
REGISTRY_KNOWN_HOSTS = Path("/etc/infralink/registry-known_hosts")


class ControllerRuntimeError(ValueError):
    """The private controller runtime cannot safely reconcile this host."""

    def __init__(
        self,
        code: str,
        *,
        stage: str | None = None,
        exit_code: int = 78,
        failure_details: dict[str, Any] | None = None,
        publish_evidence: bool = True,
    ) -> None:
        super().__init__(code)
        self.stage = stage
        self.exit_code = exit_code
        self.failure_details = failure_details
        self.publish_evidence = publish_evidence


@dataclass(frozen=True)
class ControllerContext:
    """One fixed runtime context derived from the host controller contract."""

    host_uuid: str
    registry_remote: str
    registry_ref: str
    registry_root: Path
    runtime_root: Path
    services_root: Path
    registry_key: Path
    registry_known_hosts: Path
    host_root: Path
    textfile_directory: Path
    handoff_digest: str | None
    environment: Mapping[str, str]


def _payload(*, error: str | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "ok": error is None}
    if error is not None:
        payload["error"] = {"code": error}
    else:
        payload["result"] = result or {}
    return payload


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value:
        raise ControllerRuntimeError("controller_configuration_required")
    return value


def controller_context(environ: Mapping[str, str]) -> ControllerContext:
    """Construct the non-selecting controller context from fixed host paths."""

    return ControllerContext(
        host_uuid=_required(environ, "INFRALINK_HOST_UUID"),
        registry_remote=_required(environ, "INFRALINK_REGISTRY_REPO_URL"),
        registry_ref=_required(environ, "INFRALINK_REGISTRY_REF"),
        registry_root=REGISTRY_ROOT,
        runtime_root=RUNTIME_ROOT,
        services_root=SERVICES_ROOT,
        registry_key=REGISTRY_KEY,
        registry_known_hosts=REGISTRY_KNOWN_HOSTS,
        host_root=Path(environ.get("INFRALINK_HOST_ROOT", "/")),
        textfile_directory=Path(
            environ.get(
                "INFRALINK_NODE_EXPORTER_TEXTFILE_DIR",
                "/var/lib/node_exporter/textfile_collector",
            )
        ),
        handoff_digest=environ.get("INFRALINK_CONTROLLER_HANDOFF_DIGEST") or None,
        environment=environ,
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _deployment(context: ControllerContext) -> Path:
    return context.registry_root / "hosts" / context.host_uuid / "operations" / "deployment.yml"


def _runtime_directories(context: ControllerContext) -> None:
    """Materialize only Registry-declared runtime directories before renders."""

    try:
        directories = controller_runtime_directories._directories(_deployment(context))
        # The outer controller has host bind mounts for the allowed runtime
        # roots. Static launcher assets use ``host_root`` separately.
        controller_runtime_directories._preflight(Path("/"), directories)
        controller_runtime_directories._materialize(Path("/"), directories)
    except controller_runtime_directories.RuntimeDirectoryError as error:
        raise ControllerRuntimeError(
            "runtime_directories_failed", stage="runtime_directories"
        ) from error


@contextmanager
def _reconcile_lock(context: ControllerContext):
    """Serialize one host's runtime mutation without selecting desired state."""

    import fcntl

    context.runtime_root.mkdir(parents=True, exist_ok=True)
    lock = (context.runtime_root / "reconcile.lock").open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ControllerRuntimeError(
                "controller_reconcile_in_progress", publish_evidence=False
            ) from error
        yield
    finally:
        lock.close()


def _publish_failure(context: ControllerContext, error: ControllerRuntimeError) -> None:
    """Best-effort, bounded failure evidence; it never changes desired state."""

    if not context.runtime_root.is_dir() or not context.textfile_directory.is_dir():
        return
    try:
        controller_reconcile_evidence.write_failure(
            SimpleNamespace(
                runtime_root=context.runtime_root,
                textfile_directory=context.textfile_directory,
                host_uuid=context.host_uuid,
                reason_code=str(error),
                failure_details_json=_failure_details_json(error),
                observed_at=_utcnow(),
            )
        )
    except controller_reconcile_evidence.EvidenceError:
        return


def _failure_details_json(error: ControllerRuntimeError) -> str | None:
    if error.failure_details is not None:
        return json.dumps(error.failure_details, separators=(",", ":"))
    if error.stage is None:
        return None
    return json.dumps(
        {
            "stage": error.stage,
            "exit_code": error.exit_code,
            "diagnostic_code": str(error),
        },
        separators=(",", ":"),
    )


def _publish_success(
    context: ControllerContext,
    *,
    revision: str,
    controller_digest: str,
    adapter: dict[str, Any],
    cleanup: dict[str, Any],
) -> None:
    """Commit success evidence only after every generic apply stage completed."""

    try:
        controller_reconcile_evidence.write_success(
            SimpleNamespace(
                runtime_root=context.runtime_root,
                textfile_directory=context.textfile_directory,
                host_uuid=context.host_uuid,
                registry_revision=revision,
                registry_ref=context.registry_ref,
                registry_repo_url=context.registry_remote,
                controller_reference=controller_digest,
                controller_digest=controller_digest.rsplit("@", 1)[1],
                adapter_json=json.dumps(adapter, separators=(",", ":")),
                observed_at=_utcnow(),
                docker_image_cleanup_json=json.dumps(cleanup, separators=(",", ":")),
            )
        )
    except controller_reconcile_evidence.EvidenceError as error:
        raise ControllerRuntimeError("reconcile_evidence_failed") from error


def _controller_digest(reference: str, *, pull: bool) -> str:
    """Resolve one declared controller reference to its local immutable digest."""

    source = reference.split("@", 1)[0]
    repository = source if "@" in reference else source.rsplit(":", 1)[0]
    if pull:
        pulled = subprocess.run(
            ["docker", "pull", reference], text=True, capture_output=True, check=False
        )
        if pulled.returncode:
            raise ControllerRuntimeError(
                "controller_image_pull_failed",
                stage="controller_image",
                exit_code=pulled.returncode,
            )
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", reference],
        text=True,
        capture_output=True,
        check=False,
    )
    if inspected.returncode:
        raise ControllerRuntimeError(
            "controller_image_resolution_failed",
            stage="controller_image",
            exit_code=inspected.returncode,
        )
    try:
        candidates = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        raise ControllerRuntimeError(
            "controller_image_resolution_failed", stage="controller_image"
        ) from error
    matches = sorted(
        value
        for value in candidates
        if isinstance(value, str)
        and re.fullmatch(r".+@sha256:[0-9a-f]{64}", value) is not None
        and value.startswith(f"{repository}@")
    )
    if len(matches) != 1:
        raise ControllerRuntimeError("controller_image_resolution_failed", stage="controller_image")
    return matches[0]


def _handoff(
    context: ControllerContext,
    *,
    revision: str,
    controller_digest: str,
    controller_reference: str,
) -> None:
    """Run the exact selected controller image; the outer image never applies state."""

    environment = {
        **context.environment,
        "INFRALINK_CONTROLLER_HANDOFF_DIGEST": controller_digest,
        "INFRALINK_CONTROLLER_HANDOFF_REGISTRY_REVISION": revision,
        "INFRALINK_CONTROLLER_HANDOFF_REFERENCE": controller_reference,
    }
    command = [
        "docker",
        "run",
        "--rm",
        "--network=host",
        "--pid=host",
        "--privileged",
    ]
    inherited = {
        "INFRALINK_HOST_UUID",
        "BWS_ACCESS_TOKEN",
        "INFRALINK_REGISTRY_REPO_URL",
        "INFRALINK_REGISTRY_REF",
    }
    explicit = {
        "INFRALINK_CONTROLLER_HANDOFF_DIGEST",
        "INFRALINK_CONTROLLER_HANDOFF_REGISTRY_REVISION",
        "INFRALINK_CONTROLLER_HANDOFF_REFERENCE",
    }
    for key in sorted(inherited):
        if environment.get(key):
            command.extend(["-e", key])
    for key in sorted(explicit):
        command.extend(["-e", f"{key}={environment[key]}"])
    for source, destination, readonly in (
        (Path("/var/lib"), Path("/var/lib"), False),
        (context.services_root, context.services_root, False),
        (Path("/var/log"), Path("/var/log"), False),
        (Path("/run"), Path("/run"), False),
        (REGISTRY_KEY, REGISTRY_KEY, True),
        (REGISTRY_KNOWN_HOSTS, REGISTRY_KNOWN_HOSTS, True),
        (Path("/var/run/docker.sock"), Path("/var/run/docker.sock"), False),
        (Path("/root/.docker/config.json"), Path("/root/.docker/config.json"), True),
    ):
        mount = f"type=bind,src={source},dst={destination}"
        command.extend(["--mount", f"{mount},readonly" if readonly else mount])
    completed = subprocess.run([*command, controller_digest, "reconcile"], check=False)
    if completed.returncode:
        # The inner runtime already wrote its typed evidence for operational
        # failure. The outer handoff must not overwrite it with a generic code.
        raise ControllerRuntimeError(
            "controller_handoff_failed",
            stage="controller_handoff",
            exit_code=completed.returncode,
            publish_evidence=completed.returncode != 78,
        )


def _secret_environment(context: ControllerContext, revision: str) -> dict[str, str]:
    try:
        exports = controller_render_secrets.resolve(
            registry=context.registry_root, registry_revision=revision, host_id=context.host_uuid
        )
    except controller_render_secrets.RenderSecretsError as error:
        raise ControllerRuntimeError("render_secrets_failed", stage="render_secrets") from error
    values: dict[str, str] = {}
    for export in exports:
        key, separator, raw_value = export.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ControllerRuntimeError("render_secrets_failed", stage="render_secrets")
        try:
            parsed = shlex.split(raw_value)
        except ValueError as error:
            raise ControllerRuntimeError("render_secrets_failed", stage="render_secrets") from error
        if len(parsed) != 1:
            raise ControllerRuntimeError("render_secrets_failed", stage="render_secrets")
        values[key] = parsed[0]
    return values


def _config_consumers(
    context: ControllerContext,
    revision: str,
    phase: str,
    paths: list[str],
    *,
    services_dir: Path | None = None,
    consumer_ids: tuple[str, ...] = (),
) -> None:
    selected_services = context.services_root if services_dir is None else services_dir
    payload, status = controller_config_consumers.main(
        [
            phase,
            "--deployment",
            str(_deployment(context)),
            "--compose",
            str(selected_services / "docker-compose.yml"),
            "--config-root",
            str(selected_services / "config"),
            "--changed-paths-json",
            json.dumps(paths, separators=(",", ":")),
            "--consumer-ids-json",
            json.dumps(consumer_ids, separators=(",", ":")),
        ]
    )
    if status or payload.get("ok") is not True:
        raise ControllerRuntimeError("config_consumers_failed", stage="config_consumers")


def _validate_compose(compose: Path) -> None:
    """Reject an invalid rendered Compose document before firewall mutation."""

    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose),
            "config",
            "--quiet",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ControllerRuntimeError(
            "compose_validation_failed", stage="compose_validation", exit_code=completed.returncode
        )


def _render_firewall(context: ControllerContext, revision: str, *, compose: Path) -> str | None:
    payload, status = controller_firewall.main(
        [
            "render",
            "--registry",
            str(context.registry_root),
            "--registry-revision",
            revision,
            "--uuid",
            context.host_uuid,
            "--compose",
            str(compose),
        ]
    )
    if status or payload.get("ok") is not True:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        details = error.get("details") if isinstance(error, dict) else None
        raise ControllerRuntimeError(
            code if isinstance(code, str) else "firewall_render_failed",
            stage="firewall_render",
            failure_details=details if isinstance(details, dict) else None,
        )
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("status") == "disabled":
        return None
    rules = result.get("rules")
    if not isinstance(rules, str):
        raise ControllerRuntimeError("firewall_render_failed", stage="firewall_render")
    return rules


def _apply_firewall_rules(rules: str | None) -> None:
    if rules is None:
        return
    present = subprocess.run(
        ["nft", "list", "table", "inet", "infralink_filter"],
        text=True,
        capture_output=True,
        check=False,
    )
    if present.returncode:
        created = subprocess.run(
            ["nft", "add", "table", "inet", "infralink_filter"],
            text=True,
            capture_output=True,
            check=False,
        )
        if created.returncode:
            raise ControllerRuntimeError(
                "firewall_apply_failed", stage="firewall_apply", exit_code=created.returncode
            )
    completed = subprocess.run(
        ["nft", "-f", "-"], input=rules, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise ControllerRuntimeError(
            "firewall_apply_failed", stage="firewall_apply", exit_code=completed.returncode
        )


def _apply_firewall(context: ControllerContext, revision: str) -> None:
    """Render and install the declared firewall for legacy direct callers."""

    _apply_firewall_rules(
        _render_firewall(context, revision, compose=context.services_root / "docker-compose.yml")
    )


def _v2_artifact_catalogs(registry: Path) -> tuple[Path, ...]:
    """Return the selected checkout's complete V2 artifact catalog set."""

    root = registry / "service-catalog" / "v2"
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.yml") if path.is_file()))


def _project_artifacts(
    context: ControllerContext, revision: str, *, services_dir: Path
) -> tuple[list[str], tuple[str, ...]]:
    """Materialize every declared artifact family into one selected projection."""

    generic_paths = controller_artifacts.apply(
        registry=context.registry_root,
        registry_revision=revision,
        host_id=context.host_uuid,
        services_dir=services_dir,
    )
    catalogs = _v2_artifact_catalogs(context.registry_root)
    if not catalogs:
        return generic_paths, ()
    typed = typed_artifact_materializer.materialize_v2_artifact_bindings(
        registry=context.registry_root,
        expected_revision=revision,
        host_id=context.host_uuid,
        services_dir=services_dir,
        source_paths=catalogs,
    )
    return sorted(set(generic_paths) | set(typed.changed_paths)), typed.affected_consumers


@contextmanager
def _staged_services(context: ControllerContext):
    """Create an ephemeral candidate projection for one locked reconcile."""

    context.runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="projection-", dir=context.runtime_root) as directory:
        yield Path(directory) / "services"


def _compose_services(compose: Path, *, excluded: set[str]) -> list[str]:
    try:
        document = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        services = document["services"]
    except (KeyError, OSError, yaml.YAMLError, TypeError) as error:
        raise ControllerRuntimeError("compose_invalid") from error
    if not isinstance(services, dict) or any(not isinstance(name, str) for name in services):
        raise ControllerRuntimeError("compose_invalid")
    return sorted(set(services) - excluded)


def _apply_compose(context: ControllerContext, *, excluded: set[str]) -> int:
    compose = context.services_root / "docker-compose.yml"
    services = _compose_services(compose, excluded=excluded)
    if not services:
        _remove_orphans(compose)
        return 0
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose),
            "up",
            "-d",
            "--remove-orphans",
            "--no-deps",
            *services,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ControllerRuntimeError(
            "compose_apply_failed", stage="compose", exit_code=completed.returncode
        )
    return len(services)


def _remove_orphans(compose: Path) -> None:
    """Remove only containers outside the declared Compose service set."""

    declared = set(_compose_services(compose, excluded=set()))
    listed = subprocess.run(
        ["docker", "compose", "-f", str(compose), "ps", "--all", "--format", "json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if listed.returncode:
        raise ControllerRuntimeError("compose_orphan_inspection_failed", stage="compose")
    raw = listed.stdout.strip()
    if not raw:
        return
    try:
        records = json.loads(raw)
        if isinstance(records, dict):
            records = [records]
    except json.JSONDecodeError:
        try:
            records = [json.loads(line) for line in raw.splitlines()]
        except json.JSONDecodeError as error:
            raise ControllerRuntimeError(
                "compose_orphan_inspection_failed", stage="compose"
            ) from error
    if not isinstance(records, list):
        raise ControllerRuntimeError("compose_orphan_inspection_failed", stage="compose")
    identifiers: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ControllerRuntimeError("compose_orphan_inspection_failed", stage="compose")
        service, identifier = record.get("Service"), record.get("ID")
        if not isinstance(service, str) or not isinstance(identifier, str):
            raise ControllerRuntimeError("compose_orphan_inspection_failed", stage="compose")
        if service not in declared:
            if re.fullmatch(r"[0-9a-f]{12,64}", identifier) is None:
                raise ControllerRuntimeError("compose_orphan_inspection_failed", stage="compose")
            identifiers.append(identifier)
    if not identifiers:
        return
    removed = subprocess.run(
        ["docker", "rm", "-f", *sorted(set(identifiers))],
        text=True,
        capture_output=True,
        check=False,
    )
    if removed.returncode:
        raise ControllerRuntimeError("compose_orphan_removal_failed", stage="compose")


def _inner_reconcile(
    context: ControllerContext, revision: str, controller_digest: str
) -> dict[str, Any]:
    """Apply generic Ops stages from one already-verified registry checkout."""

    try:
        verify_registry_revision(context.registry_root, expected_revision=revision)
        declared_controller = resolve_controller_reference(
            context.registry_root, context.host_uuid, expected_revision=revision
        )
        handoff_reference = _required(context.environment, "INFRALINK_CONTROLLER_HANDOFF_REFERENCE")
        if handoff_reference != declared_controller or (
            _controller_digest(declared_controller, pull=False) != controller_digest
        ):
            raise ControllerRuntimeError("controller_handoff_invalid")
        images = resolve_host_images(
            context.registry_root, context.host_uuid, expected_revision=revision
        )
        secret_values = _secret_environment(context, revision)
        render_kwargs = {
            "registry": context.registry_root,
            "host_id": context.host_uuid,
            "expected_registry_revision": revision,
            "resolved_images": images,
            "environment_values": {
                **context.environment,
                **secret_values,
                "SELF_DEPLOY_GIT_SHA": revision,
            },
        }

        # All declarative projection checks run against this ephemeral candidate.
        # It is derived from the selected Registry revision and discarded; it is
        # not a persisted plan or another desired-state selector. This is a
        # preflight boundary: invalid render, artifact, Compose, protected
        # transition, or firewall declarations cannot change live services.
        with _staged_services(context) as staged_services:
            template_renderer.render_declared_host(**render_kwargs, services_dir=staged_services)
            _project_artifacts(context, revision, services_dir=staged_services)
            staged_compose = staged_services / "docker-compose.yml"
            _validate_compose(staged_compose)
            transition, transition_status = controller_protected_transitions.validate(
                registry=context.registry_root,
                registry_revision=revision,
                host_id=context.host_uuid,
                compose=staged_compose,
            )
            rules = _render_firewall(context, revision, compose=staged_compose)

        if transition_status or not isinstance(transition, dict):
            raise ControllerRuntimeError(
                "protected_transition_failed", stage="protected_transition"
            )
        representation = transition.get("representation_equivalent", [])
        if not isinstance(representation, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("service"), str)
            for item in representation
        ):
            raise ControllerRuntimeError(
                "protected_transition_failed", stage="protected_transition"
            )

        rendered = template_renderer.render_declared_host(
            **render_kwargs, services_dir=context.services_root
        )
        artifact_paths, typed_consumers = _project_artifacts(
            context, revision, services_dir=context.services_root
        )
        changed_paths = sorted(set(rendered.changed_config_paths) | set(artifact_paths))
        _validate_compose(context.services_root / "docker-compose.yml")
        _config_consumers(
            context, revision, "validate", changed_paths, consumer_ids=typed_consumers
        )
        _apply_firewall_rules(rules)
        service_count = _apply_compose(
            context, excluded={item["service"] for item in representation}
        )
        _config_consumers(
            context, revision, "activate", changed_paths, consumer_ids=typed_consumers
        )
    except ControllerRuntimeError:
        raise
    except (
        RegistryCheckoutError,
        ImageResolutionError,
        template_renderer.TemplateRenderError,
        controller_artifacts.ControllerArtifactsError,
        typed_artifact_materializer.TypedArtifactMaterializationError,
    ) as error:
        raise ControllerRuntimeError("controller_reconcile_failed", stage="projection") from error

    actions = [
        {
            "category": "render",
            "state": "changed" if changed_paths else "unchanged",
            "count": len(changed_paths),
        },
        {
            "category": "artifact",
            "state": "changed" if artifact_paths else "unchanged",
            "count": len(artifact_paths),
        },
        {"category": "firewall", "state": "changed", "count": 1},
        {
            "category": "service",
            "state": "changed" if service_count else "skipped",
            "count": service_count,
        },
    ]
    return {
        "phase": "apply",
        "status": "applied",
        "registry_revision": revision,
        "actions": actions,
        "evidence": [],
    }


def reconcile(context: ControllerContext) -> dict[str, Any]:
    """Run the sole controller lifecycle without a legacy adapter fallback."""

    try:
        if context.handoff_digest is None:
            with _reconcile_lock(context):
                context.services_root.mkdir(parents=True, exist_ok=True)
                checkout = fetch_configured_registry(
                    context.registry_root,
                    configured_remote=context.registry_remote,
                    configured_ref=context.registry_ref,
                    identity_file=context.registry_key,
                    known_hosts_file=context.registry_known_hosts,
                )
                _runtime_directories(context)
                try:
                    controller_host_interface.refresh(context.host_root)
                except controller_host_interface.HostInterfaceError as error:
                    raise ControllerRuntimeError(
                        "host_interface_refresh_failed", stage="host_interface"
                    ) from error
                reference = resolve_controller_reference(
                    checkout.root, context.host_uuid, expected_revision=checkout.revision
                )
                controller_digest = _controller_digest(reference, pull=True)
            _handoff(
                context,
                revision=checkout.revision,
                controller_digest=controller_digest,
                controller_reference=reference,
            )
            return {"status": "handoff_completed", "registry_revision": checkout.revision}
        revision = _required(context.environment, "INFRALINK_CONTROLLER_HANDOFF_REGISTRY_REVISION")
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
            r".+@sha256:[0-9a-f]{64}", context.handoff_digest
        ):
            raise ControllerRuntimeError("controller_handoff_invalid")
        with _reconcile_lock(context):
            result = _inner_reconcile(context, revision, context.handoff_digest)
            cleanup, cleanup_status = controller_images.prune_unused_images("docker")
            if cleanup_status:
                raise ControllerRuntimeError("docker_image_cleanup_failed")
            _publish_success(
                context,
                revision=revision,
                controller_digest=context.handoff_digest,
                adapter=result,
                cleanup=cleanup,
            )
        result["docker_image_cleanup"] = cleanup
        return result
    except ControllerRuntimeError:
        raise
    except (OSError, RegistryCheckoutError, ImageResolutionError) as error:
        raise ControllerRuntimeError("controller_reconcile_failed", stage="outer") from error


def main(
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], int]:
    """Run one private controller-image mode."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    runtime_environment = os.environ if environ is None else environ
    if arguments and arguments[0] == "doctor":
        return controller_doctor.main(arguments[1:])
    if arguments == ["bootstrap"] and runtime_environment.get("INFRALINK_BOOTSTRAP_APPLY") == "1":
        return controller_bootstrap.main(
            ["apply", "--host-root", runtime_environment.get("INFRALINK_HOST_ROOT", "/host")],
            environ=runtime_environment,
        )
    if arguments != ["reconcile"]:
        return _payload(error="usage_error"), 64
    try:
        context = controller_context(runtime_environment)
        return _payload(result=reconcile(context)), 0
    except ControllerRuntimeError as error:
        if "context" in locals() and error.publish_evidence:
            _publish_failure(context, error)
        status = 64 if str(error) == "controller_configuration_required" else 78
        return _payload(error=str(error)), status


if __name__ == "__main__":
    payload, status = main()
    import yaml

    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    raise SystemExit(status)
