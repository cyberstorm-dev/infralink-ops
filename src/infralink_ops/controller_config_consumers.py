"""Validate and activate Compose consumers of controller-owned configuration."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "infralink.ops.config-consumers/v1"
SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConfigConsumerError(ValueError):
    """An explicit config-consumer request cannot be completed."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid usage in the runnable response contract."""

    def error(self, message: str) -> None:
        raise ConfigConsumerError("usage_error")


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigConsumerError("config_consumers_invalid")
    return value


def _consumer(value: object) -> dict[str, Any]:
    consumer = _mapping(value)
    identifier = consumer.get("id")
    prefix = consumer.get("path_prefix")
    service = consumer.get("service")
    validation_argv = consumer.get("validation_argv")
    lifecycle = consumer.get("lifecycle")
    if (
        not isinstance(identifier, str)
        or not identifier
        or not isinstance(prefix, str)
        or not prefix
        or prefix.startswith("/")
        or ".." in Path(prefix).parts
        or not isinstance(service, str)
        or not SERVICE_NAME.fullmatch(service)
        or not isinstance(validation_argv, list)
        or not validation_argv
        or any(not isinstance(item, str) or not item for item in validation_argv)
        or lifecycle != "compose-recreate"
    ):
        raise ConfigConsumerError("config_consumers_invalid")
    return {
        "id": identifier,
        "path_prefix": prefix.rstrip("/") + "/",
        "service": service,
        "validation_argv": validation_argv,
    }


def _changed_paths(value: str) -> list[str]:
    try:
        paths = json.loads(value)
    except json.JSONDecodeError as error:
        raise ConfigConsumerError("changed_paths_invalid") from error
    if not isinstance(paths, list) or any(
        not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts
        for path in paths
    ):
        raise ConfigConsumerError("changed_paths_invalid")
    return paths


def _config_root(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir():
        raise ConfigConsumerError("config_root_invalid")
    return path.resolve()


def _run(compose: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["docker", "compose", "-f", str(compose), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ConfigConsumerError("config_consumer_activation_failed")


def _direct_file_binds(compose: Path, config_root: Path) -> list[tuple[str, Path, str]]:
    document = _mapping(yaml.safe_load(compose.read_text(encoding="utf-8")) or {})
    services = document.get("services")
    if not isinstance(services, dict):
        raise ConfigConsumerError("compose_invalid")
    binds: list[tuple[str, Path, str]] = []
    for service, raw_service in services.items():
        if not isinstance(service, str) or not SERVICE_NAME.fullmatch(service):
            raise ConfigConsumerError("compose_invalid")
        volumes = _mapping(raw_service).get("volumes", [])
        if not isinstance(volumes, list):
            raise ConfigConsumerError("compose_invalid")
        for volume in volumes:
            source: str | None = None
            target: str | None = None
            if isinstance(volume, str):
                parts = volume.split(":")
                if len(parts) >= 2:
                    source, target = parts[:2]
            elif isinstance(volume, dict) and volume.get("type") == "bind":
                candidate = volume.get("source")
                source = candidate if isinstance(candidate, str) else None
                candidate = volume.get("target")
                target = candidate if isinstance(candidate, str) else None
            if not source or not target or not source.startswith("/"):
                continue
            source_path = Path(source).resolve(strict=False)
            try:
                source_path.relative_to(config_root)
            except ValueError:
                continue
            if source_path.is_dir():
                continue
            if not source_path.is_file():
                raise ConfigConsumerError("direct_file_bind_source_unavailable")
            binds.append((service, source_path, target))
    return binds


def _direct_file_bind_services(
    compose: Path, config_root: Path, changed_paths: list[str]
) -> list[str]:
    changed_sources = {(config_root / path).resolve(strict=False) for path in changed_paths}
    return list(
        dict.fromkeys(
            service
            for service, source, _ in _direct_file_binds(compose, config_root)
            if source in changed_sources
        )
    )


def _container_file_matches(compose: Path, service: str, source: Path, target: str) -> bool | None:
    listed = subprocess.run(
        ["docker", "compose", "-f", str(compose), "ps", "-q", service],
        text=True,
        capture_output=True,
        check=False,
    )
    container_ids = list(dict.fromkeys(item for item in listed.stdout.splitlines() if item))
    if listed.returncode:
        raise ConfigConsumerError("direct_bind_inspection_failed")
    # A completed one-shot service has no live bind mount to inspect. It can
    # still be selected explicitly when its declared source changes.
    if not container_ids:
        return None
    declared = source.read_bytes()
    for container_id in container_ids:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "mounted-file"
            copied_result = subprocess.run(
                ["docker", "cp", f"{container_id}:{target}", str(copied)],
                capture_output=True,
                check=False,
            )
            if copied_result.returncode or not copied.is_file():
                raise ConfigConsumerError("direct_bind_inspection_failed")
            if copied.read_bytes() != declared:
                return False
    return True


def _stale_direct_file_bind_services(
    compose: Path, config_root: Path, *, skip_services: list[str]
) -> list[str]:
    skipped = set(skip_services)
    return list(
        dict.fromkeys(
            service
            for service, source, target in _direct_file_binds(compose, config_root)
            if service not in skipped
            if _container_file_matches(compose, service, source, target) is False
        )
    )


def _payload(
    *,
    command: str | None,
    deployment: Path | None,
    compose: Path | None,
    config_root: Path | None,
    changed_paths: list[str] | None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    if deployment is not None:
        arguments["deployment"] = str(deployment)
    if compose is not None:
        arguments["compose"] = str(compose)
    if config_root is not None:
        arguments["config_root"] = str(config_root)
    if changed_paths is not None:
        arguments["changed_paths"] = changed_paths
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command else [], "args": arguments},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result or {"consumers": [], "services": []}
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Execute one explicit config-consumer operation."""

    parser = EnvelopeParser(prog="controller-config-consumers")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in (commands.add_parser("validate"), commands.add_parser("activate")):
        command.add_argument("--deployment", required=True, type=Path)
        command.add_argument("--compose", required=True, type=Path)
        command.add_argument("--config-root", required=True, type=Path)
        command.add_argument("--changed-paths-json", required=True)
    try:
        args = parser.parse_args(argv)
    except ConfigConsumerError as error:
        return _payload(
            command=None,
            deployment=None,
            compose=None,
            config_root=None,
            changed_paths=None,
            error=str(error),
        ), 64

    changed_paths: list[str] | None = None
    try:
        changed_paths = _changed_paths(args.changed_paths_json)
        config_root = _config_root(args.config_root)
        deployment = _mapping(yaml.safe_load(args.deployment.read_text(encoding="utf-8")) or {})
        raw_consumers = deployment.get("rendered_config_consumers", [])
        if not isinstance(raw_consumers, list):
            raise ConfigConsumerError("config_consumers_invalid")
        consumers = [_consumer(value) for value in raw_consumers]
        if len({consumer["id"] for consumer in consumers}) != len(consumers):
            raise ConfigConsumerError("config_consumers_invalid")
        affected = [
            consumer
            for consumer in consumers
            if any(path.startswith(consumer["path_prefix"]) for path in changed_paths)
        ]
        if args.command == "validate":
            for consumer in affected:
                _run(
                    args.compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    consumer["service"],
                    *consumer["validation_argv"],
                )
            selected_services = [consumer["service"] for consumer in affected]
        else:
            changed_bind_services = _direct_file_bind_services(
                args.compose, config_root, changed_paths
            )
            selected_services = list(
                dict.fromkeys(
                    [consumer["service"] for consumer in affected]
                    + changed_bind_services
                    + _stale_direct_file_bind_services(
                        args.compose,
                        config_root,
                        skip_services=[consumer["service"] for consumer in affected]
                        + changed_bind_services,
                    )
                )
            )
            if selected_services:
                _run(
                    args.compose,
                    "up",
                    "-d",
                    "--no-deps",
                    "--force-recreate",
                    *selected_services,
                )
    except (ConfigConsumerError, OSError, yaml.YAMLError):
        return _payload(
            command=args.command,
            deployment=args.deployment,
            compose=args.compose,
            config_root=args.config_root,
            changed_paths=changed_paths,
            error="config_consumers_failed",
        ), 78

    return _payload(
        command=args.command,
        deployment=args.deployment,
        compose=args.compose,
        config_root=args.config_root,
        changed_paths=changed_paths,
        result={
            "consumers": [consumer["id"] for consumer in affected],
            "services": selected_services,
        },
    ), 0
