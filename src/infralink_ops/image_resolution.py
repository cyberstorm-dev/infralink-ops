"""Resolve registry-declared container image selectors to immutable references."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,383}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,383}@sha256:[0-9a-f]{64}$")


class ImageResolutionError(RuntimeError):
    """The declared image selector could not be resolved safely."""


def canonical_repository(repository: str) -> str:
    """Return a fully qualified OCI repository name."""

    first, separator, remainder = repository.partition("/")
    if not separator:
        return f"docker.io/library/{repository}"
    if first == "docker.io":
        return repository if "/" in remainder else f"docker.io/library/{remainder}"
    if "." not in first and ":" not in first and first != "localhost":
        return f"docker.io/{repository}"
    return repository


def repository_aliases(repository: str) -> set[str]:
    aliases = {repository}
    if repository.startswith("docker.io/"):
        relative = repository.removeprefix("docker.io/")
        aliases.add(relative)
        if relative.startswith("library/"):
            aliases.add(relative.removeprefix("library/"))
    return aliases


def _fail(message: str) -> None:
    raise ImageResolutionError(message)


def selector_reference(name: object, selector: object) -> tuple[str, str, str, str]:
    """Resolve declaration precedence: SHA, explicit tag, then branch."""

    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        _fail("image map contains an invalid service name")
    if isinstance(selector, str) and _REFERENCE.fullmatch(selector) is not None:
        configured_repository, digest = selector.rsplit("@", 1)
        repository = canonical_repository(configured_repository)
        return name, repository, f"{repository}@{digest}", selector
    if not isinstance(selector, dict):
        _fail(f"image {name} must declare repository and exactly one selector")
    configured_repository = selector.get("repository")
    if (
        not isinstance(configured_repository, str)
        or _REPOSITORY.fullmatch(configured_repository) is None
    ):
        _fail(f"image {name} repository is invalid")
    repository = canonical_repository(configured_repository)
    if "sha" in selector:
        value = selector["sha"]
        if not isinstance(value, str) or _SHA.fullmatch(value) is None:
            _fail(f"image {name} sha selector is invalid")
        return (
            name,
            repository,
            f"{repository}@sha256:{value}",
            f"{configured_repository}@sha256:{value}",
        )
    if "tag" in selector and selector["tag"] != "head":
        value = selector["tag"]
        if (
            not isinstance(value, str)
            or not value
            or any(character.isspace() or character == "@" for character in value)
        ):
            _fail(f"image {name} selector is invalid")
        return name, repository, f"{repository}:{value}", f"{configured_repository}:{value}"
    value = selector.get("branch", "main")
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or character == "@" for character in value)
    ):
        _fail(f"image {name} selector is invalid")
    return name, repository, f"{repository}:{value}", f"{configured_repository}:{value}"


def resolved_reference(reference: str, repository: str, *, docker: str = "docker") -> str:
    """Pull a selector twice when mutable, then return its only matching digest."""

    try:
        subprocess.run([docker, "pull", reference], check=True, capture_output=True)
        inspected = subprocess.run(
            [
                docker,
                "image",
                "inspect",
                "--format",
                "{{range .RepoDigests}}{{println .}}{{end}}",
                reference,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "") or ""
        _fail(f"image {reference} could not be resolved: {stderr.strip()}")
    candidates = [line.strip() for line in inspected.stdout.splitlines()]
    matches = [
        value
        for value in candidates
        if _REFERENCE.fullmatch(value) is not None
        and value.rsplit("@", 1)[0] in repository_aliases(repository)
    ]
    if len(matches) != 1:
        _fail(f"image {reference} did not resolve to one immutable digest")
    return f"{repository}@sha256:{matches[0].rsplit(':', 1)[1]}"


def resolve_host_image_evidence(
    registry_root: Path, host_id: str, *, expected_revision: str, docker: str = "docker"
) -> dict[str, dict[str, str]]:
    """Resolve a host image map and retain configured references as evidence."""

    try:
        root = verify_registry_revision(registry_root, expected_revision=expected_revision).root
    except RegistryCheckoutError as error:
        _fail(str(error))
    deployment_path = root / "hosts" / host_id / "operations" / "deployment.yml"
    try:
        document = yaml.safe_load(deployment_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        _fail(f"deployment declaration is unavailable: {error}")
    if not isinstance(document, dict):
        _fail("deployment declaration is invalid")
    images = document.get("images", {})
    if not isinstance(images, dict):
        _fail("deployment image map is invalid")
    resolved: dict[str, str] = {}
    configured: dict[str, str] = {}
    for name, selector in sorted(images.items()):
        image_name, repository, reference, configured_reference = selector_reference(name, selector)
        first = resolved_reference(reference, repository, docker=docker)
        if "@sha256:" not in reference:
            second = resolved_reference(reference, repository, docker=docker)
            if second != first:
                _fail(f"image {reference} changed during resolution")
        resolved[image_name] = first
        configured[image_name] = configured_reference
    return {"configured": configured, "resolved": resolved}


def resolve_host_images(
    registry_root: Path, host_id: str, *, expected_revision: str, docker: str = "docker"
) -> dict[str, str]:
    """Resolve a host's declared image map to immutable OCI references."""

    return resolve_host_image_evidence(
        registry_root, host_id, expected_revision=expected_revision, docker=docker
    )["resolved"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="infralink-controller-image-resolution")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--format", choices=("resolved", "evidence"), default="resolved")
    args = parser.parse_args(argv)
    try:
        evidence = resolve_host_image_evidence(
            args.registry, args.uuid, expected_revision=args.expected_revision
        )
        result: object = evidence if args.format == "evidence" else evidence["resolved"]
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    except ImageResolutionError as error:
        print(f"controller images: {error}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
