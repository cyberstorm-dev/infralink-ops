"""Discover declared Gitlink roots required by one registry host."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .registry_checkout import RegistryCheckoutError, verify_registry_revision

SCHEMA_VERSION = "infralink.ops.template-source-dependencies/v1"


class TemplateSourceDependencyError(ValueError):
    """The selected Registry revision has an invalid template-source declaration."""


def declared_template_source_submodules(
    *, registry: Path, expected_revision: str, host_uuid: str
) -> tuple[str, ...]:
    """Return selected top-level Gitlink roots for one declared host source set."""

    root = verify_registry_revision(registry, expected_revision=expected_revision).root
    sources = _declared_sources(root, host_uuid)
    roots: set[str] = set()
    for source in sources:
        for index in range(len(source.parts), 0, -1):
            candidate = PurePosixPath(*source.parts[:index])
            if _is_gitlink(root, expected_revision, candidate):
                roots.add(str(candidate))
                break
    return tuple(sorted(roots))


def _declared_sources(registry: Path, host_uuid: str) -> tuple[PurePosixPath, ...]:
    hosts = (registry / "hosts").resolve()
    host = (hosts / host_uuid).resolve()
    if host.parent != hosts or not host.is_dir():
        raise TemplateSourceDependencyError("template_source_manifest_invalid")
    manifest = host / "manifest.yml"
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        host = document["hosts"][host_uuid]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as error:
        raise TemplateSourceDependencyError("template_source_manifest_invalid") from error
    if not isinstance(host, dict):
        raise TemplateSourceDependencyError("template_source_manifest_invalid")
    declarations = host.get("template_sources", [])
    if not isinstance(declarations, list):
        raise TemplateSourceDependencyError("template_source_declaration_invalid")

    sources: list[PurePosixPath] = []
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {"id", "source"}:
            raise TemplateSourceDependencyError("template_source_declaration_invalid")
        identifier, raw_source = declaration["id"], declaration["source"]
        if not isinstance(identifier, str) or not identifier:
            raise TemplateSourceDependencyError("template_source_declaration_invalid")
        sources.append(_safe_source(raw_source))
    if len(set(sources)) != len(sources):
        raise TemplateSourceDependencyError("template_source_declaration_invalid")
    return tuple(sources)


def _safe_source(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise TemplateSourceDependencyError("template_source_declaration_invalid")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
    ):
        raise TemplateSourceDependencyError("template_source_declaration_invalid")
    return path


def _is_gitlink(registry: Path, revision: str, path: PurePosixPath) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(registry), "ls-tree", "-z", revision, "--", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise TemplateSourceDependencyError("template_source_discovery_failed")
    if not completed.stdout:
        return False
    entry = completed.stdout.split("\0", 1)[0]
    metadata, _, entry_path = entry.partition("\t")
    return entry_path == str(path) and metadata.startswith("160000 commit ")


def main(argv: list[str] | None = None) -> tuple[dict[str, Any], int]:
    parser = argparse.ArgumentParser(prog="infralink-controller-template-source-dependencies")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--registry-revision", required=True)
    parser.add_argument("--uuid", required=True)
    arguments = parser.parse_args(argv)
    try:
        roots = declared_template_source_submodules(
            registry=arguments.registry,
            expected_revision=arguments.registry_revision,
            host_uuid=arguments.uuid,
        )
    except RegistryCheckoutError as error:
        code = (
            "registry_revision_mismatch"
            if "revision mismatch" in str(error)
            else "registry_invalid"
        )
        return _payload(error=code), 78
    except TemplateSourceDependencyError as error:
        return _payload(error=str(error)), 78
    return _payload(result={"template_source_submodules": list(roots)}), 0


def _payload(
    *, result: dict[str, object] | None = None, error: str | None = None
) -> dict[str, Any]:
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


def cli() -> int:
    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
