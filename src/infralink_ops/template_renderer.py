"""Generic rendering of a registry-declared host compose and config tree.

The caller selects and verifies the registry checkout. This module deliberately
does not fetch a revision or retain desired state; it turns that one selected
checkout into controller-owned files.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml
from infralink.observation import ProjectValidationError, project_v2_configuration_bindings
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from infralink_ops.declared_file_destination import (
    DeclaredFileDestinationError,
    repair_empty_declared_file_destination,
)
from infralink_ops.template_sources import (
    DeclaredTemplateSourceLoader,
    TemplateSourceError,
    load_template_sources,
)

_IMMUTABLE_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,383}@sha256:[0-9a-f]{64}$")


class TemplateRenderError(ValueError):
    """A host declaration cannot be rendered safely."""


@dataclass(frozen=True)
class TemplateRenderResult:
    """Files changed while rendering a declared host."""

    changed_config_paths: tuple[str, ...]
    compose_changed: bool


class RelativeIncludeEnvironment(Environment):
    """Resolve a bare include name relative to its parent template."""

    def join_path(self, template: str, parent: str) -> str:
        if "/" not in template and "/" in parent:
            return f"{parent.rsplit('/', 1)[0]}/{template}"
        return template


def load_resolved_images(value: str) -> dict[str, str]:
    """Decode a fully-qualified, immutable resolved-image mapping."""

    try:
        images = json.loads(value)
    except json.JSONDecodeError as error:
        raise TemplateRenderError("resolved image map is invalid") from error
    return validate_resolved_images(images)


def validate_resolved_images(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(name, str)
        or not isinstance(image, str)
        or _IMMUTABLE_IMAGE.fullmatch(image) is None
        for name, image in value.items()
    ):
        raise TemplateRenderError("resolved image map is invalid")
    return dict(value)


def load_host(registry: Path, host_id: str) -> dict[str, object]:
    manifest = registry / "hosts" / host_id / "manifest.yml"
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise TemplateRenderError(f"host manifest is unavailable: {host_id}") from error
    hosts = data.get("hosts") if isinstance(data, dict) else None
    host = hosts.get(host_id) if isinstance(hosts, dict) else None
    if not isinstance(host, dict):
        raise TemplateRenderError(f"host manifest does not declare {host_id}")
    return host


def load_host_configuration_bindings(
    *, registry: Path, host_id: str
) -> dict[str, dict[str, list[dict[str, object]]]]:
    """Project V2 configuration bindings for one host into a template-safe mapping.

    The Registry remains the only desired-state source.  This function only
    groups the public Infralink projection by profile and slot, retaining the
    service-instance identity so templates never rely on implicit precedence.
    """

    catalog = registry / "service-catalog" / "v2"
    if not catalog.exists():
        return {}
    if not catalog.is_dir() or catalog.is_symlink():
        raise TemplateRenderError("v2 service catalog is invalid")
    sources = tuple(sorted(path for path in catalog.glob("*.yml") if path.is_file()))
    if not sources:
        return {}
    try:
        projected = project_v2_configuration_bindings(sources)
    except ProjectValidationError as error:
        raise TemplateRenderError("v2 configuration bindings are invalid") from error

    configuration: dict[str, dict[str, list[dict[str, object]]]] = {}
    for binding in projected.configuration_bindings:
        if binding.host_id != host_id:
            continue
        slots = configuration.setdefault(binding.profile_id, {})
        entries = slots.setdefault(binding.slot_id, [])
        entries.append(
            {
                "service_instance_id": binding.service_instance_id,
                "component_id": binding.component_id,
                "value": binding.value,
            }
        )
    return configuration


def generic_jinja_environment(
    registry: Path,
    host_dir: Path,
    *,
    expected_registry_revision: str | None,
    host: Mapping[str, object],
) -> Environment:
    sources = load_template_sources(
        registry=registry, expected_revision=expected_registry_revision, host=host
    )
    env = RelativeIncludeEnvironment(
        loader=ChoiceLoader(
            [
                DeclaredTemplateSourceLoader(sources),
                FileSystemLoader(
                    [str(host_dir), str(registry / "hosts" / "_templates"), str(registry / "hosts")]
                ),
            ]
        ),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    register_generic_jinja_helpers(env)
    return env


def register_generic_jinja_helpers(env: Environment) -> Environment:
    env.filters.update(
        {
            "dsn_host": dsn_host,
            "dsn_port": dsn_port,
            "dsn_username": dsn_username,
            "dsn_password": dsn_password,
            "dsn_database": dsn_database,
            "dsn_url": dsn_url,
            "dsn_with_database": dsn_with_database,
            "from_json": from_json,
            "nginx_quoted": nginx_quoted,
        }
    )
    return env


def render_declared_host(
    *,
    registry: Path,
    host_id: str,
    services_dir: Path,
    resolved_images: Mapping[str, str],
    expected_registry_revision: str | None = None,
    context: Mapping[str, object] | None = None,
    environment_values: Mapping[str, str] | None = None,
) -> TemplateRenderResult:
    """Render generic host declarations from a caller-selected registry checkout.

    Template sources are declared by the host manifest and are bound to the
    caller-selected registry revision. Private callback extensions are not part
    of this renderer's contract.
    """

    registry = registry.resolve()
    host_dir = registry / "hosts" / host_id
    host = load_host(registry, host_id)
    images = validate_resolved_images(dict(resolved_images))
    permissions = load_rendered_config_permissions(host)
    try:
        env = generic_jinja_environment(
            registry, host_dir, expected_registry_revision=expected_registry_revision, host=host
        )
    except TemplateSourceError as error:
        raise TemplateRenderError(str(error)) from error
    render_context: dict[str, object] = {
        **(os.environ if environment_values is None else environment_values),
        **(context or {}),
        "host": host,
        "uuid": host_id,
        "canonical_name": host.get("canonical_name", host_id),
        "values": {},
        "configuration": load_host_configuration_bindings(registry=registry, host_id=host_id),
        "images": images,
    }
    services_dir.mkdir(parents=True, exist_ok=True)
    compose = _render_template(env, "docker-compose.yml.j2", render_context).encode("utf-8")
    compose_changed = _atomic_write(services_dir / "docker-compose.yml", compose)

    managed_config = services_dir / "config"
    state_file = services_dir / ".infralink-managed-config.json"
    previous_paths = _load_managed_paths(state_file)
    config_sources = _declared_config_sources(host_dir)
    desired_paths = {relative_string for _, _, _, relative_string in config_sources}
    unknown_permissions = sorted(set(permissions) - desired_paths)
    if unknown_permissions:
        raise TemplateRenderError(
            "rendered config permission does not name a declared file: "
            + ", ".join(unknown_permissions)
        )

    changed_paths: list[str] = []
    for source, relative, destination_relative, relative_string in config_sources:
        destination = managed_config / destination_relative
        if source.suffix == ".j2":
            template_name = (Path("config") / relative).as_posix()
            body = _render_template(env, template_name, render_context).encode("utf-8")
        else:
            body = source.read_bytes()
        if _write_declared_config(destination, managed_config, body):
            changed_paths.append(relative_string)
        if relative_string in permissions:
            apply_rendered_config_permissions(destination, permissions[relative_string])

    for relative_string in sorted(previous_paths - desired_paths, reverse=True):
        destination = (managed_config / relative_string).resolve()
        if managed_config.resolve() not in destination.parents:
            raise TemplateRenderError("managed config state escapes config root")
        if destination.is_file() or destination.is_symlink():
            destination.unlink()
            _prune_empty_parents(destination.parent, managed_config)
            changed_paths.append(relative_string)

    _atomic_write(
        state_file,
        json.dumps(sorted(desired_paths), separators=(",", ":")).encode("utf-8"),
    )
    return TemplateRenderResult(tuple(changed_paths), compose_changed)


def load_rendered_config_permissions(
    host: Mapping[str, object],
) -> dict[str, tuple[int, int, int]]:
    value = host.get("rendered_config_permissions", [])
    if not isinstance(value, list):
        raise TemplateRenderError("rendered config permissions must be a list")
    permissions: dict[str, tuple[int, int, int]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise TemplateRenderError("rendered config permission must be a mapping")
        path, mode, uid, gid = (
            item.get("path"),
            item.get("mode"),
            item.get("owner_uid"),
            item.get("owner_gid"),
        )
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(path).parts)
            or not isinstance(mode, str)
            or re.fullmatch(r"0[0-7]{3}", mode) is None
            or not isinstance(uid, int)
            or isinstance(uid, bool)
            or uid < 0
            or not isinstance(gid, int)
            or isinstance(gid, bool)
            or gid < 0
            or path in permissions
        ):
            raise TemplateRenderError("rendered config permission is invalid")
        permissions[path] = (int(mode, 8), uid, gid)
    return permissions


def apply_rendered_config_permissions(destination: Path, permission: tuple[int, int, int]) -> None:
    mode, uid, gid = permission
    os.chown(destination, uid, gid)
    os.chmod(destination, mode)


def _render_template(env: Environment, name: str, context: Mapping[str, object]) -> str:
    try:
        return env.get_template(name).render(**context)
    except Exception as error:
        raise TemplateRenderError(f"template render failed: {name}") from error


def _declared_config_sources(host_dir: Path) -> list[tuple[Path, Path, Path, str]]:
    config_root = host_dir / "config"
    if not config_root.exists():
        return []
    if not config_root.is_dir() or config_root.is_symlink():
        raise TemplateRenderError("declared config root is invalid")
    sources: list[tuple[Path, Path, Path, str]] = []
    for source in sorted(path for path in config_root.rglob("*") if path.is_file()):
        if source.is_symlink():
            raise TemplateRenderError("declared config source must not be a symlink")
        relative = source.relative_to(config_root)
        destination_relative = relative.with_suffix("") if relative.suffix == ".j2" else relative
        sources.append((source, relative, destination_relative, destination_relative.as_posix()))
    return sources


def _atomic_write(destination: Path, body: bytes) -> bool:
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise TemplateRenderError(f"managed_destination_invalid: {destination}")
    if destination.is_file() and destination.read_bytes() == body:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        stream.write(body)
        staged = Path(stream.name)
    staged.replace(destination)
    return True


def _write_declared_config(destination: Path, root: Path, body: bytes) -> bool:
    try:
        destination = repair_empty_declared_file_destination(root, destination.relative_to(root))
    except DeclaredFileDestinationError as error:
        raise TemplateRenderError(f"{error}: {destination}") from error
    return _atomic_write(destination, body)


def _load_managed_paths(path: Path) -> set[str]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise TemplateRenderError(f"managed_destination_invalid: {path}")
    if not path.exists():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TemplateRenderError("managed config state is malformed") from error
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TemplateRenderError("managed config state is malformed")
    return set(value)


def _prune_empty_parents(path: Path, root: Path) -> None:
    while path != root:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _dsn_parts(value: str) -> dict[str, str]:
    raw = (value or "").strip()
    if "://" not in raw:
        return {key: "" for key in ("host", "port", "username", "password", "database")}
    _, remainder = raw.split("://", 1)
    userinfo, separator, remainder = remainder.rpartition("@")
    if not separator:
        remainder, userinfo = userinfo, ""
    database = ""
    if "/" in remainder:
        remainder, database = remainder.split("/", 1)
        database = database.split("?", 1)[0]
    username, password = (userinfo.split(":", 1) + [""])[:2] if userinfo else ("", "")
    host, port = _host_port(remainder)
    return {
        "host": host,
        "port": port,
        "username": unquote(username),
        "password": unquote(password),
        "database": database,
    }


def _host_port(value: str) -> tuple[str, str]:
    if value.startswith("[") and "]" in value:
        end = value.index("]")
        return value[1:end], value[end + 2 :] if value[end + 1 :].startswith(":") else ""
    return tuple(value.rsplit(":", 1)) if ":" in value else (value, "")


def dsn_host(value: str) -> str:
    return _dsn_parts(value)["host"]


def dsn_port(value: str) -> str:
    return _dsn_parts(value)["port"]


def dsn_username(value: str) -> str:
    return _dsn_parts(value)["username"]


def dsn_password(value: str) -> str:
    return _dsn_parts(value)["password"]


def dsn_database(value: str) -> str:
    return _dsn_parts(value)["database"]


def dsn_url(value: str) -> str:
    return (value or "").strip()


def dsn_with_database(value: str, database: str) -> str:
    raw, target = (value or "").strip(), (database or "").strip()
    if not raw or not target or "://" not in raw:
        return raw
    scheme, remainder = raw.split("://", 1)
    authority_start = remainder.rfind("@") + 1
    path_index = remainder.find("/", authority_start)
    netloc, path = (
        (remainder, "")
        if path_index == -1
        else (remainder[:path_index], remainder[path_index + 1 :])
    )
    suffix = ""
    if "#" in path:
        path, fragment = path.split("#", 1)
        suffix = f"#{fragment}"
    if "?" in path:
        _, query = path.split("?", 1)
        suffix = f"?{query}{suffix}"
    return f"{scheme}://{netloc}/{target}{suffix}"


def from_json(value: str) -> object:
    raw = (value or "").strip()
    return {} if not raw else json.loads(raw)


def nginx_quoted(value: object) -> str:
    raw = str(value)
    if "\n" in raw or "\r" in raw:
        raise TemplateRenderError("Nginx quoted values cannot contain newlines")
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'
