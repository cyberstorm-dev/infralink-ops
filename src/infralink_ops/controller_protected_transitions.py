"""Validate explicitly authorized protected-service image transitions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision

_DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
Identity = dict[str, str]
DeclaredTransition = tuple[Identity, Identity, Identity, Identity]


class ProtectedTransitionError(ValueError):
    """A protected image transition is invalid or unauthorized."""


def canonical_repository(value: str) -> str:
    """Return the fully qualified OCI repository for a configured reference."""

    first, separator, remainder = value.partition("/")
    if not separator:
        return f"docker.io/library/{value}"
    if first == "docker.io" and "/" not in remainder:
        return f"docker.io/library/{remainder}"
    if "." not in first and ":" not in first and first != "localhost":
        return f"docker.io/{value}"
    return value


def canonical_reference(value: object) -> str:
    """Validate and canonicalize one immutable OCI digest reference."""

    if not isinstance(value, str) or _DIGEST_REFERENCE.fullmatch(value) is None:
        raise ProtectedTransitionError("protected_transition_digest_required")
    repository, digest = value.rsplit("@", 1)
    return f"{canonical_repository(repository)}@{digest}"


def canonical_configured_reference(value: object) -> str:
    """Validate and canonicalize a configured immutable reference or tag."""

    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ProtectedTransitionError("protected_transition_configured_reference_invalid")
    if "@sha256:" in value:
        return canonical_reference(value)
    repository, separator, tag = value.rpartition(":")
    if not separator or "/" in tag or _TAG.fullmatch(tag) is None:
        repository = value
        tag = ""
    if not repository or "@" in repository:
        raise ProtectedTransitionError("protected_transition_configured_reference_invalid")
    return f"{canonical_repository(repository)}{':' + tag if tag else ''}"


def _run(docker: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([docker, *arguments], capture_output=True, text=True, check=False)
    except OSError as error:
        return subprocess.CompletedProcess([docker, *arguments], returncode=125, stderr=str(error))


def _live_identity(docker: str, compose: Path, service: str) -> Identity | None:
    container = _run(docker, "compose", "-f", str(compose), "ps", "-q", "--all", service)
    if container.returncode != 0:
        raise ProtectedTransitionError("protected_transition_live_identity_unavailable")
    container_id = container.stdout.strip()
    if not container_id:
        return None
    configured = _run(docker, "inspect", "--format", "{{.Config.Image}}", container_id)
    image_id = _run(docker, "inspect", "--format", "{{.Image}}", container_id)
    if configured.returncode != 0 or image_id.returncode != 0:
        raise ProtectedTransitionError("protected_transition_live_identity_unavailable")
    digests = _run(
        docker,
        "image",
        "inspect",
        "--format",
        "{{range .RepoDigests}}{{println .}}{{end}}",
        image_id.stdout.strip(),
    )
    values = [value.strip() for value in digests.stdout.splitlines() if value.strip()]
    if digests.returncode != 0 or len(values) != 1:
        raise ProtectedTransitionError("protected_transition_live_identity_unavailable")
    return {
        "configured": canonical_configured_reference(configured.stdout.strip()),
        "resolved": canonical_reference(values[0]),
    }


def _declared_identity(value: object) -> tuple[Identity, Identity]:
    if not isinstance(value, dict):
        raise ProtectedTransitionError("protected_transition_declaration_invalid")
    configured = value.get("configured")
    resolved = value.get("resolved")
    if not isinstance(configured, str) or not isinstance(resolved, str):
        raise ProtectedTransitionError("protected_transition_declaration_invalid")
    return (
        {"configured": configured, "resolved": resolved},
        {
            "configured": canonical_configured_reference(configured),
            "resolved": canonical_reference(resolved),
        },
    )


def validate(
    *, registry: Path, registry_revision: str, host_id: str, compose: Path, docker: str = "docker"
) -> tuple[dict[str, object], int]:
    """Validate protected-image transitions against an exact registry revision."""

    try:
        checkout = verify_registry_revision(registry, expected_revision=registry_revision)
        deployment_path = checkout.root / "hosts" / host_id / "operations" / "deployment.yml"
        deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8")) or {}
        document = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        services = deployment.get("services", {}) if isinstance(deployment, dict) else None
        protected = services.get("protected", []) if isinstance(services, dict) else None
        components = document.get("services", {}) if isinstance(document, dict) else None
        transitions = (
            deployment.get("protected_image_transitions", [])
            if isinstance(deployment, dict)
            else None
        )
        if (
            not isinstance(protected, list)
            or any(not isinstance(name, str) or not name for name in protected)
            or not isinstance(components, dict)
            or not isinstance(transitions, list)
        ):
            raise ProtectedTransitionError("protected_transition_declaration_invalid")
        declared: dict[str, DeclaredTransition] = {}
        for transition in transitions:
            if not isinstance(transition, dict):
                raise ProtectedTransitionError("protected_transition_declaration_invalid")
            service = transition.get("service")
            if not isinstance(service, str) or service not in protected or service in declared:
                raise ProtectedTransitionError("protected_transition_declaration_invalid")
            before, canonical_before = _declared_identity(transition.get("before"))
            after, canonical_after = _declared_identity(transition.get("after"))
            declared[service] = (before, after, canonical_before, canonical_after)
        evidence: list[dict[str, object]] = []
        representation_equivalent: list[dict[str, object]] = []
        for service in protected:
            component = components.get(service)
            if not isinstance(component, dict):
                raise ProtectedTransitionError("protected_transition_service_missing")
            desired = {
                "configured": canonical_configured_reference(component.get("image")),
                "resolved": canonical_reference(component.get("image")),
            }
            current = _live_identity(docker, compose, service)
            if current is None or current == desired:
                continue
            if current["resolved"] == desired["resolved"]:
                representation_equivalent.append(
                    {
                        "service": service,
                        "configured": {
                            "live": current["configured"],
                            "desired": desired["configured"],
                        },
                        "resolved": desired["resolved"],
                    }
                )
                continue
            authorization = declared.get(service)
            if authorization is None or authorization[2:] != (current, desired):
                raise ProtectedTransitionError("protected_transition_unauthorized")
            evidence.append(
                {"service": service, "before": authorization[0], "after": authorization[1]}
            )
        return {
            "registry_revision": checkout.revision,
            "transitions": evidence,
            "representation_equivalent": representation_equivalent,
        }, 0
    except RegistryCheckoutError:
        return {"error": "registry_checkout_failed"}, 78
    except (OSError, yaml.YAMLError, ProtectedTransitionError) as error:
        return {"error": str(error)}, 78
