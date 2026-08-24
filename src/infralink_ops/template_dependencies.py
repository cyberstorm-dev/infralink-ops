"""Discover a declared host's Jinja template graph without rendering it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, meta

from .registry_checkout import RegistryCheckoutError, verify_registry_revision

SCHEMA_VERSION = "infralink.ops.template-dependencies/v1"


class TemplateDependencyError(ValueError):
    """A declared Jinja template graph is unavailable or invalid."""


class RelativeIncludeEnvironment(Environment):
    """Resolve a bare include relative to the including template."""

    def join_path(self, template: str, parent: str) -> str:
        if "/" not in template and "/" in parent:
            return f"{parent.rsplit('/', 1)[0]}/{template}"
        return template


def _initial_templates(host: Path) -> set[str]:
    compose = host / "docker-compose.yml.j2"
    if not compose.is_file():
        raise TemplateDependencyError("template_dependency_unavailable")
    templates = {"docker-compose.yml.j2"}
    config = host / "config"
    if config.is_dir():
        templates.update(
            str(Path("config") / path.relative_to(config)) for path in config.rglob("*.j2")
        )
    return templates


def discover_template_dependencies(
    *, registry: Path, expected_revision: str, host_uuid: str
) -> tuple[str, ...]:
    """Return all reachable Jinja templates as stable registry-relative paths."""

    root = verify_registry_revision(registry, expected_revision=expected_revision).root
    hosts = (root / "hosts").resolve()
    host = (hosts / host_uuid).resolve()
    if host.parent != hosts or not host.is_dir():
        raise TemplateDependencyError("template_dependency_unavailable")
    loader = FileSystemLoader([str(host), str(root / "hosts" / "_templates"), str(root / "hosts")])
    environment = RelativeIncludeEnvironment(loader=loader)
    pending = list(_initial_templates(host))
    visited: set[str] = set()
    resolved: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        try:
            source, filename, _ = loader.get_source(environment, name)
            parsed = environment.parse(source)
        except (OSError, TemplateNotFound, ValueError) as error:
            raise TemplateDependencyError("template_dependency_unavailable") from error
        if filename is None:
            raise TemplateDependencyError("template_dependency_unavailable")
        try:
            resolved.add(str(Path(filename).resolve().relative_to(root)))
        except ValueError as error:
            raise TemplateDependencyError("template_dependency_unavailable") from error
        for reference in meta.find_referenced_templates(parsed):
            if not isinstance(reference, str):
                raise TemplateDependencyError("template_dependency_unresolved")
            pending.append(environment.join_path(reference, name))
    return tuple(sorted(resolved))


def _payload(*, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = {"code": error}
    return payload


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    """Emit one typed, registry-relative template graph."""

    parser = argparse.ArgumentParser(prog="infralink-controller-template-dependencies")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--registry-revision", required=True)
    parser.add_argument("--host-uuid", required=True)
    arguments = parser.parse_args(argv)
    try:
        templates = discover_template_dependencies(
            registry=arguments.registry,
            expected_revision=arguments.registry_revision,
            host_uuid=arguments.host_uuid,
        )
    except (RegistryCheckoutError, TemplateDependencyError) as error:
        return _payload(error=str(error)), 78
    return _payload(result={"templates": list(templates)}), 0


def cli() -> int:
    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
