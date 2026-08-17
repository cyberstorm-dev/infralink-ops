"""Load provisionable Grafana dashboards from a revision-pinned registry catalog."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml


def load_registry_dashboards(
    registry_root: Path,
    *,
    expected_revision: str,
    datasource: str,
    catalog_path: str = "service-catalog/dashboards.yml",
) -> tuple[dict[str, Any], ...]:
    """Load dashboard definitions solely from a verified registry revision.

    The returned documents are ready for a downstream provisioner. This function
    deliberately performs no filesystem writes or Grafana API calls.
    """

    root = _verified_registry_root(registry_root, expected_revision)
    catalog_relative_path = _registry_relative_path(root, catalog_path, "dashboard catalog")
    catalog = _load_yaml(_git_file(root, expected_revision, catalog_relative_path), catalog_path)
    if (
        not isinstance(catalog, dict)
        or catalog.get("schema_version") != "infralink.dashboard-catalog/v1"
    ):
        raise ValueError(f"invalid dashboard catalog schema: {catalog_path}")
    entries = catalog.get("dashboards")
    if not isinstance(entries, list):
        raise ValueError(f"dashboard catalog must contain a dashboards list: {catalog_path}")

    rendered: list[dict[str, Any]] = []
    for entry in entries:
        rendered.append(
            _load_dashboard_entry(
                root,
                expected_revision=expected_revision,
                entry=entry,
                datasource=datasource,
            )
        )
    return tuple(rendered)


def _load_dashboard_entry(
    root: Path,
    *,
    expected_revision: str,
    entry: Any,
    datasource: str,
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("dashboard catalog entries must be mappings")
    dashboard_id = _required_string(entry, "id", "dashboard catalog entry")
    profile_id = _required_string(entry, "profile_id", f"dashboard {dashboard_id}")
    grafana = entry.get("grafana")
    if not isinstance(grafana, dict):
        raise ValueError(f"dashboard {dashboard_id} requires a grafana mapping")
    uid = _required_string(grafana, "uid", f"dashboard {dashboard_id}.grafana")
    datasource_input = _required_string(
        grafana, "datasource_input", f"dashboard {dashboard_id}.grafana"
    )
    asset_path = _registry_relative_path(
        root,
        _required_string(grafana, "asset", f"dashboard {dashboard_id}.grafana"),
        "dashboard asset",
    )
    upstream = grafana.get("upstream")
    if not isinstance(upstream, dict):
        raise ValueError(f"dashboard {dashboard_id}.grafana requires upstream metadata")
    expected_sha256 = _required_string(
        upstream, "sha256", f"dashboard {dashboard_id}.grafana.upstream"
    )
    if len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256
    ):
        raise ValueError(f"dashboard {dashboard_id} has invalid asset sha256")

    asset_bytes = _git_file(root, expected_revision, asset_path)
    actual_sha256 = hashlib.sha256(asset_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"dashboard asset sha256 mismatch: {asset_path} expected {expected_sha256}, "
            f"got {actual_sha256}"
        )
    try:
        dashboard = json.loads(asset_bytes)
    except json.JSONDecodeError as error:
        raise ValueError(f"dashboard asset is not valid JSON: {asset_path}") from error
    if not isinstance(dashboard, dict):
        raise ValueError(f"dashboard asset must contain a JSON object: {asset_path}")

    stamped = _replace_datasource_placeholder(dashboard, f"${{{datasource_input}}}", datasource)
    stamped["uid"] = uid
    return {"id": dashboard_id, "profile_id": profile_id, "dashboard": stamped}


def _verified_registry_root(registry_root: Path, expected_revision: str) -> Path:
    root = registry_root.resolve()
    if not root.is_dir():
        raise ValueError(f"registry root must be a directory: {registry_root}")
    actual_revision = _git_revision(root)
    if actual_revision != expected_revision:
        raise ValueError(
            "registry revision mismatch: "
            f"expected {expected_revision}, checkout has {actual_revision}"
        )
    return root


def _registry_relative_path(root: Path, value: str, description: str) -> str:
    candidate = (root / value).resolve()
    if root not in candidate.parents:
        raise ValueError(f"{description} must exist below registry root: {value}")
    return str(candidate.relative_to(root))


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry checkout has no readable Git HEAD: {root}")
    return completed.stdout.strip()


def _git_file(root: Path, revision: str, relative_path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{relative_path}"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"registry revision does not contain required file: {relative_path}")
    return completed.stdout


def _load_yaml(content: bytes, path: str) -> Any:
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError(f"dashboard catalog is not valid YAML: {path}") from error


def _required_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} requires non-empty {key}")
    return value


def _replace_datasource_placeholder(value: Any, placeholder: str, datasource: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_datasource_placeholder(item, placeholder, datasource)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_datasource_placeholder(item, placeholder, datasource) for item in value]
    if isinstance(value, str):
        return value.replace(placeholder, datasource)
    return value
