"""Resolve registry-declared render-secret bindings through the BWS CLI."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class RenderSecretsError(ValueError):
    """A declared render-secret binding cannot be resolved safely."""


def _mapping(value: object, *, error: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderSecretsError(error)
    return value


def _deployment(registry: Path, host_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f-]{36}", host_id):
        raise RenderSecretsError("host_id_invalid")
    try:
        return _mapping(
            yaml.safe_load(
                (registry / "hosts" / host_id / "operations" / "deployment.yml").read_text(
                    encoding="utf-8"
                )
            )
            or {},
            error="deployment_invalid",
        )
    except (OSError, yaml.YAMLError) as error:
        raise RenderSecretsError("deployment_unavailable") from error


def _secrets(registry: Path, deployment: dict[str, Any]) -> dict[str, Any] | None:
    declaration = deployment.get("render_secrets")
    if declaration is None:
        return None
    declaration = _mapping(declaration, error="render_secrets_invalid")
    relative_path = declaration.get("path")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or relative_path.startswith("/")
        or ".." in Path(relative_path).parts
    ):
        raise RenderSecretsError("render_secrets_invalid")
    try:
        return _mapping(
            yaml.safe_load((registry / relative_path).read_text(encoding="utf-8")) or {},
            error="render_secrets_invalid",
        )
    except (OSError, yaml.YAMLError) as error:
        raise RenderSecretsError("render_secrets_unavailable") from error


def _project_values(project_id: str, alias: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["bws", "secret", "list", project_id, "--output", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise RenderSecretsError("bws_unavailable") from error
    if result.returncode:
        raise RenderSecretsError(f"project_unavailable:{alias}")
    try:
        listed = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RenderSecretsError(f"project_response_invalid:{alias}") from error
    if not isinstance(listed, list):
        raise RenderSecretsError(f"project_response_invalid:{alias}")
    return {
        item["key"]: str(item.get("value", ""))
        for item in listed
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def resolve(*, registry: Path, host_id: str) -> list[str]:
    """Return shell-safe exports for the host's declared render-secret bindings."""

    document = _secrets(registry, _deployment(registry, host_id))
    if document is None:
        return []
    raw_projects = document.get("projects")
    if not isinstance(raw_projects, list):
        raise RenderSecretsError("project_mapping_invalid")
    projects: list[tuple[str, str]] = []
    for entry in raw_projects:
        entry = _mapping(entry, error="project_mapping_invalid")
        alias = entry.get("alias")
        project_id = entry.get("project_id")
        if (
            not isinstance(alias, str)
            or not alias
            or not isinstance(project_id, str)
            or not project_id
        ):
            raise RenderSecretsError("project_mapping_invalid")
        projects.append((alias, project_id))
    if not projects or len({alias for alias, _ in projects}) != len(projects):
        raise RenderSecretsError("project_mapping_invalid")
    values = {alias: _project_values(project_id, alias) for alias, project_id in projects}

    bindings = document.get("bindings", [])
    if not isinstance(bindings, list):
        raise RenderSecretsError("render_binding_invalid")
    exports: list[str] = []
    for binding in bindings:
        binding = _mapping(binding, error="render_binding_invalid")
        context_key = binding.get("context_key")
        project = binding.get("project")
        secret_key = binding.get("secret_key")
        required = binding.get("required", False)
        if (
            not isinstance(context_key, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", context_key) is None
            or not isinstance(project, str)
            or project not in values
            or not isinstance(secret_key, str)
            or not secret_key
            or not isinstance(required, bool)
        ):
            raise RenderSecretsError("render_binding_invalid")
        value = values[project].get(secret_key)
        if value is None:
            if required:
                raise RenderSecretsError("required_secret_missing")
            continue
        exports.append(f"{context_key}={shlex.quote(value)}")
    return exports


def cli(argv: list[str] | None = None) -> int:
    """Write shell-safe exports for the private renderer's declared bindings."""

    parser = argparse.ArgumentParser(prog="infralink-controller-render-secrets")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--uuid", required=True)
    arguments = parser.parse_args(argv)
    try:
        for export in resolve(registry=arguments.registry, host_id=arguments.uuid):
            print(export)
    except RenderSecretsError as error:
        print(f"controller render secrets: {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
