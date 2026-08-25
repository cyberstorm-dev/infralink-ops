"""Strict, bounded Docker Compose observation wire protocol.

This module owns only Docker command construction and parsing of Docker's
formatted output.  It deliberately has no registry, desired-state, process,
or deployment-policy dependency.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

ProtocolFailureReason = Literal[
    "version_output",
    "listing_output",
    "container_output",
    "lifecycle",
    "image_invalid",
    "image_unavailable",
    "image_ambiguous",
]

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_REPO_DIGESTS, MAX_LISTING_RECORDS, MAX_INSPECT_RECORDS = 32, 512, 128
MAX_ARGV_BYTES, MAX_STDOUT_BYTES = 128 * 1024, 2 * 1024 * 1024
MAX_LINE_BYTES, MAX_STRING_BYTES = 32 * 1024, 4_096
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCAL_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DISTRIBUTION_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SERVICE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
_API_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")
_POSITIVE_DECIMAL_PATTERN = re.compile(r"^[1-9][0-9]*$")
_REPOSITORY_PATTERN = re.compile(
    r"^(?=.{1,384}$)(?:[a-z0-9][a-z0-9._-]*(?::[0-9]{1,5})?/)?[a-z0-9][a-z0-9._/-]*$"
)
_SECRET_ASSIGNMENT_START = re.compile(
    r"(?i)(?<![a-z0-9])(?P<key>-*[a-z][^\s=:,;|]*)[ \t]*(?:=|:)[ \t]*"
)
_KNOWN_TOKEN = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]+|xapp-[A-Za-z0-9-]+|glpat-[A-Za-z0-9_-]{8,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{8,})"
)
_BWS_ACCESS_TOKEN = re.compile(r"(?<!\w)0\.[A-Za-z0-9_-]{20,}")
_UNSAFE_SENTINEL = re.compile(r"(?i)\bsecret(?:_[a-z0-9]+)*_sentinel\b|\bunredacted(?:\b|_)")
_AUTH_CREDENTIAL = re.compile(r"(?i)\b(?:basic|bearer|digest)\s+(?P<value>\S+)")
_URI = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s]+")
_SAFE_SECRET_METADATA_SUFFIXES = frozenset({"available", "count", "reference", "source", "status"})
_SAFE_SECRET_STATUSES = frozenset({"expired", "missing", "redacted", "unavailable"})
_REDACTED_VALUES = frozenset(
    {"[REDACTED]", "Bearer [REDACTED]", "Basic [REDACTED]", "Digest [REDACTED]"}
)
_ASSIGNMENT_VALUE_SEPARATORS = " \t\r\n,;|"

DOCKER_PREFIX = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")
_FIELD_SEPARATOR = "\x1f"
_FIELD_SEPARATOR_ACTION = '{{printf "%c" 31}}'
VERSION_TEMPLATE = _FIELD_SEPARATOR_ACTION.join(
    (
        "{{json .Client.Version}}",
        "{{json .Server.Version}}",
        "{{json .Client.APIVersion}}",
        "{{json .Server.APIVersion}}",
    )
)
LIST_TEMPLATE = _FIELD_SEPARATOR_ACTION.join(
    (
        "{{json .ID}}",
        '{{json (.Label "com.docker.compose.project")}}',
        '{{json (.Label "com.docker.compose.service")}}',
        '{{json (.Label "com.docker.compose.container-number")}}',
        '{{json (.Label "com.docker.compose.oneoff")}}',
    )
)
CONTAINER_TEMPLATE = _FIELD_SEPARATOR_ACTION.join(
    (
        "{{json .Id}}",
        "{{json .Image}}",
        "{{json .Config.Image}}",
        "{{json .State.Status}}",
        "{{json .State.Running}}",
        "{{json .State.Paused}}",
        "{{json .State.Restarting}}",
        "{{json .State.Dead}}",
        "{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}",
        "{{json .RestartCount}}",
        '{{json (index .Config.Labels "com.docker.compose.project")}}',
        '{{json (index .Config.Labels "com.docker.compose.service")}}',
        '{{json (index .Config.Labels "com.docker.compose.container-number")}}',
        '{{json (index .Config.Labels "com.docker.compose.oneoff")}}',
    )
)
IMAGE_TEMPLATE = _FIELD_SEPARATOR_ACTION.join(("{{json .Id}}", "{{json .RepoDigests}}"))
_VERSION_WIRE = (4, 1, False, "version_output")
_LISTING_WIRE = (5, MAX_LISTING_RECORDS, True, "listing_output")
_CONTAINER_WIRE = (14, MAX_INSPECT_RECORDS, False, "container_output")
_IMAGE_WIRE = (2, MAX_INSPECT_RECORDS, False, "image_invalid")


class ComposeStateProtocolError(RuntimeError):
    """Docker output failed the bounded public observation protocol."""

    def __init__(self, reason: ProtocolFailureReason) -> None:
        self.reason = reason
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(reason={self.reason!r})"


@dataclass(frozen=True, slots=True)
class DistributionIdentity:
    image: str
    digest: str


@dataclass(frozen=True, slots=True)
class ConfiguredImage:
    reference: str
    repository: str
    digest: str | None


@dataclass(frozen=True, slots=True)
class DockerVersion:
    client_version: str
    server_version: str
    negotiated_api_version: str
    server_api_version: str


@dataclass(frozen=True, slots=True)
class ListedContainer:
    container_id: str
    project: str
    service: str
    container_number: int
    oneoff: bool


@dataclass(frozen=True, slots=True)
class InspectedContainer:
    container_id: str
    local_image_id: str
    configured_image: ConfiguredImage
    lifecycle: Literal["stopped", "running"]
    health_status: Literal["starting", "healthy", "unhealthy"] | None
    restart_count: int
    project: str
    service: str
    container_number: int
    oneoff: bool


@dataclass(frozen=True, slots=True)
class InspectedImage:
    local_image_id: str
    repo_digests: tuple[str, ...]


def _is_sensitive_key(value: str) -> bool:
    normalized = value.strip().lstrip("-")
    normalized = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", normalized)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    parts = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").lower().split("_")
    if parts[-1:] and parts[-1] in _SAFE_SECRET_METADATA_SUFFIXES:
        return False
    if any(part in {"apikey", "apikeys"} for part in parts):
        return True
    if any(
        left in {"api", "private"} and right in {"key", "keys"} for left, right in pairwise(parts)
    ):
        return True
    return any(
        part
        in {
            "authorization",
            "credential",
            "credentials",
            "password",
            "passwords",
            "secret",
            "secrets",
            "token",
            "tokens",
        }
        for part in parts
    )


def _contains_secret_without_urls(value: str) -> bool:
    if (
        _KNOWN_TOKEN.search(value)
        or _BWS_ACCESS_TOKEN.search(value)
        or _UNSAFE_SENTINEL.search(value)
    ):
        return True
    assignments = list(_SECRET_ASSIGNMENT_START.finditer(value))
    for index, match in enumerate(assignments):
        if not _is_sensitive_key(match.group("key")):
            continue
        end = assignments[index + 1].start() if index + 1 < len(assignments) else len(value)
        assigned = value[match.end() : end].strip(_ASSIGNMENT_VALUE_SEPARATORS)
        if assigned not in _REDACTED_VALUES and assigned.lower() not in _SAFE_SECRET_STATUSES:
            return True
    return any(match.group("value") != "[REDACTED]" for match in _AUTH_CREDENTIAL.finditer(value))


def _contains_secret_material(value: str) -> bool:
    if _contains_secret_without_urls(value):
        return True
    candidates = [match.group() for match in _URI.finditer(value)]
    if value.startswith("//"):
        candidates.append(value)
    for candidate in candidates:
        parsed = urlsplit(candidate)
        if parsed.password is not None:
            return True
        if parsed.username is not None and (
            not parsed.scheme or _contains_secret_without_urls(unquote(parsed.username))
        ):
            return True
        for component in (parsed.query, parsed.fragment):
            if component and (
                _contains_secret_without_urls(component)
                or any(
                    _is_sensitive_key(key) or _contains_secret_without_urls(item)
                    for key, item in parse_qsl(component, keep_blank_values=True)
                )
            ):
                return True
    return False


def _fail(reason: ProtocolFailureReason) -> None:
    raise ComposeStateProtocolError(reason)


def _plain_string(value: object, reason: ProtocolFailureReason, *, bounded: bool = False) -> str:
    if type(value) is not str:
        _fail(reason)
    try:
        if bounded and len(value.encode("utf-8")) > MAX_STRING_BYTES:
            _fail(reason)
    except UnicodeError:
        _fail(reason)
    return value


def _project(value: object, reason: ProtocolFailureReason) -> str:
    text = _plain_string(value, reason)
    if _PROJECT_PATTERN.fullmatch(text) is None:
        _fail(reason)
    return text


def _service(value: object, reason: ProtocolFailureReason) -> str:
    text = _plain_string(value, reason)
    if _SERVICE_PATTERN.fullmatch(text) is None:
        _fail(reason)
    return text


def _bounded_command(argv: tuple[str, ...], reason: ProtocolFailureReason) -> tuple[str, ...]:
    if type(argv) is not tuple or not argv:
        _fail(reason)
    size = 0
    for argument in argv:
        if type(argument) is not str or "\0" in argument:
            _fail(reason)
        try:
            size += len(argument.encode("utf-8")) + 1
        except UnicodeError:
            _fail(reason)
    if size > MAX_ARGV_BYTES:
        _fail(reason)
    return argv


def version_command() -> tuple[str, ...]:
    return _bounded_command(
        DOCKER_PREFIX + ("version", "--format", VERSION_TEMPLATE), "version_output"
    )


def list_command(project_name: str) -> tuple[str, ...]:
    project = _project(project_name, "listing_output")
    return _bounded_command(
        DOCKER_PREFIX
        + (
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            LIST_TEMPLATE,
        ),
        "listing_output",
    )


def _validated_ids(
    ids: tuple[str, ...], *, pattern: re.Pattern[str], reason: ProtocolFailureReason
) -> tuple[str, ...]:
    if (
        type(ids) is not tuple
        or not ids
        or len(ids) > MAX_INSPECT_RECORDS
        or any(type(item) is not str or pattern.fullmatch(item) is None for item in ids)
        or tuple(sorted(ids)) != ids
        or len(set(ids)) != len(ids)
    ):
        _fail(reason)
    return ids


def _inspect_command(ids: tuple[str, ...], *, image: bool) -> tuple[str, ...]:
    kind, template, pattern, reason = (
        ("image", IMAGE_TEMPLATE, LOCAL_IMAGE_ID_PATTERN, "image_invalid")
        if image
        else ("container", CONTAINER_TEMPLATE, CONTAINER_ID_PATTERN, "container_output")
    )
    return _bounded_command(
        DOCKER_PREFIX
        + (kind, "inspect", "--format", template)
        + _validated_ids(ids, pattern=pattern, reason=reason),
        reason,
    )


def container_inspect_command(ids: tuple[str, ...]) -> tuple[str, ...]:
    return _inspect_command(ids, image=False)


def image_inspect_command(ids: tuple[str, ...]) -> tuple[str, ...]:
    return _inspect_command(ids, image=True)


def _reject_constant(_: str) -> None:
    raise ValueError


def _decode_records(
    body: bytes, spec: tuple[int, int, bool, ProtocolFailureReason]
) -> tuple[tuple[object, ...], ...]:
    atom_count, max_records, allow_empty, reason = spec
    try:
        if type(body) is not bytes or len(body) > MAX_STDOUT_BYTES:
            raise ValueError
        if not body:
            if allow_empty:
                return ()
            raise ValueError
        if not body.endswith(b"\n") or b"\r" in body:
            raise ValueError
        lines = body[:-1].split(b"\n")
        if (
            not lines
            or len(lines) > max_records
            or any(not line or len(line) > MAX_LINE_BYTES for line in lines)
        ):
            raise ValueError
        output: list[tuple[object, ...]] = []
        for line in lines:
            atoms = line.decode("utf-8", errors="strict").split(_FIELD_SEPARATOR)
            if len(atoms) != atom_count or any(not atom or atom != atom.strip() for atom in atoms):
                raise ValueError
            decoded = tuple(json.loads(atom, parse_constant=_reject_constant) for atom in atoms)
            if any(
                type(atom) is str and len(atom.encode("utf-8")) > MAX_STRING_BYTES
                for atom in decoded
            ):
                raise ValueError
            output.append(decoded)
        return tuple(output)
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        _fail(reason)


def _container_number(value: object, reason: ProtocolFailureReason) -> int:
    text = _plain_string(value, reason)
    if _POSITIVE_DECIMAL_PATTERN.fullmatch(text) is None:
        _fail(reason)
    number = int(text)
    if number > MAX_SAFE_INTEGER:
        _fail(reason)
    return number


def _oneoff(value: object, reason: ProtocolFailureReason) -> bool:
    text = _plain_string(value, reason)
    if text not in {"False", "True"}:
        _fail(reason)
    return text == "True"


def parse_version_output(body: bytes) -> DockerVersion:
    records = _decode_records(body, _VERSION_WIRE)
    if len(records) != 1:
        _fail("version_output")
    values = tuple(_plain_string(value, "version_output") for value in records[0])
    if not all(
        (
            _VERSION_PATTERN.fullmatch(values[0]),
            _VERSION_PATTERN.fullmatch(values[1]),
            _API_VERSION_PATTERN.fullmatch(values[2]),
            _API_VERSION_PATTERN.fullmatch(values[3]),
        )
    ):
        _fail("version_output")
    return DockerVersion(*values)


def parse_listing_output(body: bytes, *, project_name: str) -> tuple[ListedContainer, ...]:
    project = _project(project_name, "listing_output")
    parsed: list[ListedContainer] = []
    seen: set[str] = set()
    for identity, raw_project, service, number, oneoff in _decode_records(body, _LISTING_WIRE):
        container_id = _plain_string(identity, "listing_output")
        record_project = _project(raw_project, "listing_output")
        if (
            CONTAINER_ID_PATTERN.fullmatch(container_id) is None
            or container_id in seen
            or record_project != project
        ):
            _fail("listing_output")
        seen.add(container_id)
        parsed.append(
            ListedContainer(
                container_id,
                record_project,
                _service(service, "listing_output"),
                _container_number(number, "listing_output"),
                _oneoff(oneoff, "listing_output"),
            )
        )
    return tuple(sorted(parsed, key=lambda item: item.container_id))


def parse_configured_image(value: object) -> ConfiguredImage:
    if type(value) is not str:
        raise ValueError("configured image must be a plain string")
    try:
        oversized = len(value.encode("utf-8")) > MAX_STRING_BYTES
    except UnicodeError:
        raise ValueError("configured image is invalid") from None
    if (
        oversized
        or any(ord(character) < 32 for character in value)
        or value.count("@") > 1
        or _contains_secret_material(value)
    ):
        raise ValueError("configured image is invalid")
    name_and_tag, separator, digest = value.rpartition("@")
    if not separator:
        name_and_tag, digest = value, None
    elif DISTRIBUTION_DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("configured image digest is invalid")
    slash, colon = name_and_tag.rfind("/"), name_and_tag.rfind(":")
    repository = name_and_tag
    if colon > slash:
        repository, tag = name_and_tag[:colon], name_and_tag[colon + 1 :]
        if _TAG_PATTERN.fullmatch(tag) is None:
            raise ValueError("configured image tag is invalid")
    if _REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("configured image repository is invalid")
    return ConfiguredImage(value, repository, digest)


def _parse_container(
    record: tuple[object, ...], requested: set[str], seen: set[str]
) -> InspectedContainer:
    identity = _plain_string(record[0], "container_output")
    if (
        CONTAINER_ID_PATTERN.fullmatch(identity) is None
        or identity in seen
        or identity not in requested
    ):
        _fail("container_output")
    seen.add(identity)
    local_image_id = _plain_string(record[1], "container_output")
    if LOCAL_IMAGE_ID_PATTERN.fullmatch(local_image_id) is None:
        _fail("container_output")
    try:
        configured = parse_configured_image(record[2])
    except (TypeError, ValueError, UnicodeError):
        _fail("container_output")
    status = _plain_string(record[3], "container_output")
    flags = record[4:8]
    if any(type(flag) is not bool for flag in flags):
        _fail("container_output")
    if flags[0] and status == "running" and not any(flags[1:]):
        lifecycle: Literal["stopped", "running"] = "running"
    elif not flags[0] and status in {"created", "exited"} and not any(flags[1:]):
        lifecycle = "stopped"
    else:
        _fail("lifecycle")
    health = record[8]
    if health is not None and health not in {"starting", "healthy", "unhealthy"}:
        _fail("container_output")
    restart_count = record[9]
    if type(restart_count) is not int or not 0 <= restart_count <= MAX_SAFE_INTEGER:
        _fail("container_output")
    return InspectedContainer(
        identity,
        local_image_id,
        configured,
        lifecycle,
        health,
        restart_count,
        _project(record[10], "container_output"),
        _service(record[11], "container_output"),
        _container_number(record[12], "container_output"),
        _oneoff(record[13], "container_output"),
    )


def parse_container_output(
    body: bytes, *, requested_ids: tuple[str, ...]
) -> tuple[InspectedContainer, ...]:
    requested = _validated_ids(
        requested_ids, pattern=CONTAINER_ID_PATTERN, reason="container_output"
    )
    seen: set[str] = set()
    parsed = [
        _parse_container(record, set(requested), seen)
        for record in _decode_records(body, _CONTAINER_WIRE)
    ]
    if seen != set(requested):
        _fail("container_output")
    return tuple(sorted(parsed, key=lambda item: item.container_id))


def _parse_repo_digest(value: object) -> DistributionIdentity:
    if type(value) is not str or value.count("@") != 1 or _contains_secret_material(value):
        _fail("image_invalid")
    repository, _, digest = value.rpartition("@")
    if (
        _REPOSITORY_PATTERN.fullmatch(repository) is None
        or DISTRIBUTION_DIGEST_PATTERN.fullmatch(digest) is None
    ):
        _fail("image_invalid")
    return DistributionIdentity(value, digest)


def parse_image_output(
    body: bytes, *, requested_ids: tuple[str, ...]
) -> tuple[InspectedImage, ...]:
    requested = _validated_ids(
        requested_ids, pattern=LOCAL_IMAGE_ID_PATTERN, reason="image_invalid"
    )
    if not body:
        _fail("image_unavailable")
    parsed: list[InspectedImage] = []
    seen: set[str] = set()
    unavailable = False
    for identity, raw_digests in _decode_records(body, _IMAGE_WIRE):
        local_image_id = _plain_string(identity, "image_invalid")
        if (
            LOCAL_IMAGE_ID_PATTERN.fullmatch(local_image_id) is None
            or local_image_id in seen
            or local_image_id not in requested
        ):
            _fail("image_invalid")
        seen.add(local_image_id)
        if raw_digests is None or raw_digests == []:
            unavailable = True
            parsed.append(InspectedImage(local_image_id, ()))
            continue
        if type(raw_digests) is not list or len(raw_digests) > MAX_REPO_DIGESTS:
            _fail("image_invalid")
        digests = tuple(_plain_string(item, "image_invalid", bounded=True) for item in raw_digests)
        for digest in digests:
            _parse_repo_digest(digest)
        parsed.append(InspectedImage(local_image_id, digests))
    if seen != set(requested) or unavailable:
        _fail("image_unavailable")
    return tuple(sorted(parsed, key=lambda item: item.local_image_id))


def select_distribution_identity(
    configured: ConfiguredImage, repo_digests: object
) -> DistributionIdentity:
    if (
        type(configured) is not ConfiguredImage
        or type(configured.reference) is not str
        or type(configured.repository) is not str
        or (configured.digest is not None and type(configured.digest) is not str)
    ):
        _fail("image_invalid")
    try:
        if parse_configured_image(configured.reference) != configured:
            _fail("image_invalid")
    except (TypeError, ValueError, UnicodeError):
        _fail("image_invalid")
    if repo_digests is None or repo_digests == ():
        _fail("image_unavailable")
    if type(repo_digests) is not tuple or not repo_digests or len(repo_digests) > MAX_REPO_DIGESTS:
        _fail("image_invalid")
    matching = {
        item
        for item in (_parse_repo_digest(value) for value in repo_digests)
        if item.image == f"{configured.repository}@{item.digest}"
    }
    if len(matching) != 1:
        _fail("image_unavailable" if not matching else "image_ambiguous")
    selected = next(iter(matching))
    if configured.digest is not None and selected.digest != configured.digest:
        _fail("image_unavailable")
    return selected
