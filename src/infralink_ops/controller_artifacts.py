"""Materialize generic Registry-declared generated artifacts."""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infralink_ops.artifact_installer import (
    ArtifactInstallError,
    ArtifactTarget,
    read_declared_artifact,
    resolve_declared_artifact_target,
)
from infralink_ops.artifact_target_install import (
    ArtifactTargetError,
    ensure_artifact_directory,
    install_artifact_body,
)
from infralink_ops.declared_file_destination import (
    DeclaredFileDestinationError,
    classify_declared_file_destination,
)
from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision
from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file

SCHEMA_VERSION = "infralink.ops.controller-artifacts/v1"
_GENERIC_PROVIDERS = {"artifact-sync", "host-config"}
_MAX_DEPLOYMENT_BYTES = 1024 * 1024


class ControllerArtifactsError(ValueError):
    """A declared generic artifact cannot be materialized safely."""


class EnvelopeParser(argparse.ArgumentParser):
    """Return invalid CLI usage in the standard response envelope."""

    def error(self, message: str) -> None:
        raise ControllerArtifactsError("usage_error")


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControllerArtifactsError("generated_artifacts_invalid")
    return value


def _deployment(registry: Path, host_id: str) -> dict[str, Any]:
    try:
        if str(uuid.UUID(host_id)) != host_id:
            raise ValueError
    except ValueError:
        raise ControllerArtifactsError("host_id_invalid")
    try:
        body = read_stable_regular_file(
            registry / "hosts" / host_id / "operations" / "deployment.yml"
        )
        if len(body) > _MAX_DEPLOYMENT_BYTES:
            raise ValueError
        document = yaml.safe_load(body) or {}
    except (StableRegularFileError, ValueError, yaml.YAMLError) as error:
        raise ControllerArtifactsError("host_deployment_unavailable") from error
    return _mapping(document)


@dataclass(frozen=True)
class _Write:
    body: bytes
    target: ArtifactTarget
    relative_path: str


def _plan_writes(registry: Path, deployment: dict[str, Any], services_dir: Path) -> list[_Write]:
    declarations = deployment.get("generated_artifacts", [])
    if not isinstance(declarations, list):
        raise ControllerArtifactsError("generated_artifacts_invalid")
    config_root = services_dir / "config"
    writes: list[_Write] = []
    for raw_declaration in declarations:
        declaration = _mapping(raw_declaration)
        provider = declaration.get("provider")
        if not isinstance(provider, str):
            raise ControllerArtifactsError("generated_artifacts_invalid")
        if provider not in _GENERIC_PROVIDERS:
            continue
        identifier = declaration.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ControllerArtifactsError("generated_artifacts_invalid")
        try:
            artifact = read_declared_artifact(registry, declaration)
            target = resolve_declared_artifact_target(services_dir, declaration)
            relative = target.destination.relative_to(config_root).as_posix()
        except (ArtifactInstallError, ValueError) as error:
            raise ControllerArtifactsError("generated_artifact_materialization_failed") from error
        writes.append(_Write(body=artifact.body, target=target, relative_path=relative))
    if len({write.relative_path for write in writes}) != len(writes):
        raise ControllerArtifactsError("generated_artifact_materialization_failed")
    for write in writes:
        try:
            classify_declared_file_destination(config_root, Path(write.relative_path))
        except DeclaredFileDestinationError as error:
            raise ControllerArtifactsError("generated_artifact_materialization_failed") from error
    return writes


def apply(*, registry: Path, registry_revision: str, host_id: str, services_dir: Path) -> list[str]:
    """Install generic generated artifacts from exactly one Registry revision."""

    checkout = verify_registry_revision(registry, expected_revision=registry_revision)
    deployment = _deployment(checkout.root, host_id)
    writes = _plan_writes(checkout.root, deployment, services_dir)
    changed: list[str] = []
    for write in writes:
        try:
            ensure_artifact_directory(write.target.destination.parent)
            did_change = install_artifact_body(
                write.target.destination,
                write.body,
                mode=write.target.mode,
                uid=write.target.owner_uid,
                gid=write.target.owner_gid,
            ).changed
        except (ArtifactTargetError, ValueError) as error:
            raise ControllerArtifactsError("generated_artifact_materialization_failed") from error
        if did_change:
            changed.append(write.relative_path)
    return sorted(set(changed))


def _payload(
    *,
    command: str | None,
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command else []},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    payload["result" if error is None else "error"] = result if error is None else {"code": error}
    return payload


def main(argv: list[str] | None = None) -> tuple[dict[str, object], int]:
    parser = EnvelopeParser(prog="infralink-controller-artifacts")
    parser.add_argument("command", choices=("apply",))
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--registry-revision", required=True)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--services-dir", required=True, type=Path)
    try:
        arguments = parser.parse_args(argv)
    except ControllerArtifactsError as error:
        return _payload(command=None, error=str(error)), 64
    try:
        changed = apply(
            registry=arguments.registry,
            registry_revision=arguments.registry_revision,
            host_id=arguments.uuid,
            services_dir=arguments.services_dir,
        )
        return _payload(command="apply", result={"changed_config_paths": changed}), 0
    except (ControllerArtifactsError, RegistryCheckoutError):
        return _payload(command="apply", error="generated_artifact_materialization_failed"), 78


def cli() -> int:
    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
