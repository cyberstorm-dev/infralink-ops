from __future__ import annotations

import subprocess
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from infralink_ops.registry_checkout import RegistryCheckoutError
from infralink_ops.template_dependencies import discover_template_dependencies

UUID = "00000000-0000-4000-8000-000000000001"


def _commit_registry(registry: Path) -> str:
    subprocess.run(["git", "init", "-q", str(registry)], check=True)
    subprocess.run(
        ["git", "-C", str(registry), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(registry), "config", "user.name", "Infralink tests"], check=True
    )
    subprocess.run(["git", "-C", str(registry), "add", "."], check=True)
    subprocess.run(["git", "-C", str(registry), "commit", "-qm", "registry"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(registry), "rev-parse", "HEAD"], text=True
    ).strip()


def test_installs_template_dependency_runnable() -> None:
    scripts = entry_points(group="console_scripts")
    command = next(
        entry for entry in scripts if entry.name == "infralink-controller-template-dependencies"
    )
    assert command.value == "infralink_ops.template_dependencies:cli"


def test_discovers_nested_and_relative_jinja_template_dependencies(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    host = registry / "hosts" / UUID
    (host / "config" / "nginx").mkdir(parents=True)
    (registry / "hosts" / "_templates").mkdir(parents=True)
    (host / "docker-compose.yml.j2").write_text(
        "{% include 'config/nginx/site.conf.j2' %}\n", encoding="utf-8"
    )
    (host / "config" / "nginx" / "site.conf.j2").write_text(
        "{% include 'shared.conf.j2' %}\n", encoding="utf-8"
    )
    (host / "config" / "nginx" / "shared.conf.j2").write_text("ok\n", encoding="utf-8")

    revision = _commit_registry(registry)

    dependencies = discover_template_dependencies(
        registry=registry, expected_revision=revision, host_uuid=UUID
    )

    assert dependencies == (
        "hosts/00000000-0000-4000-8000-000000000001/config/nginx/shared.conf.j2",
        "hosts/00000000-0000-4000-8000-000000000001/config/nginx/site.conf.j2",
        "hosts/00000000-0000-4000-8000-000000000001/docker-compose.yml.j2",
    )


def test_rejects_missing_referenced_template(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    host = registry / "hosts" / UUID
    host.mkdir(parents=True)
    (host / "docker-compose.yml.j2").write_text("{% include 'missing.j2' %}\n", encoding="utf-8")

    revision = _commit_registry(registry)

    with pytest.raises(ValueError, match="template_dependency_unavailable"):
        discover_template_dependencies(
            registry=registry, expected_revision=revision, host_uuid=UUID
        )


def test_rejects_host_reference_outside_registry_hosts_root(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    (registry / "hosts").mkdir(parents=True)
    (registry / "hosts" / ".gitkeep").write_text("\n", encoding="utf-8")
    revision = _commit_registry(registry)

    with pytest.raises(ValueError, match="template_dependency_unavailable"):
        discover_template_dependencies(
            registry=registry, expected_revision=revision, host_uuid="../outside"
        )


def test_rejects_dynamic_template_reference(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    host = registry / "hosts" / UUID
    host.mkdir(parents=True)
    (host / "docker-compose.yml.j2").write_text("{% include template_name %}\n", encoding="utf-8")
    revision = _commit_registry(registry)

    with pytest.raises(ValueError, match="template_dependency_unresolved"):
        discover_template_dependencies(
            registry=registry, expected_revision=revision, host_uuid=UUID
        )


@pytest.mark.parametrize("state", ("mismatched", "dirty"))
def test_requires_clean_checkout_at_expected_revision(tmp_path: Path, state: str) -> None:
    registry = tmp_path / "registry"
    host = registry / "hosts" / UUID
    host.mkdir(parents=True)
    compose = host / "docker-compose.yml.j2"
    compose.write_text("services: {}\n", encoding="utf-8")
    revision = _commit_registry(registry)
    expected_revision = "0" * 40
    if state == "dirty":
        expected_revision = revision
        compose.write_text("services: {changed: true}\n", encoding="utf-8")

    with pytest.raises(
        RegistryCheckoutError, match="registry (revision mismatch|checkout must be clean)"
    ):
        discover_template_dependencies(
            registry=registry, expected_revision=expected_revision, host_uuid=UUID
        )
