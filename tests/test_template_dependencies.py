from __future__ import annotations

from pathlib import Path

from infralink_ops.template_dependencies import discover_template_dependencies

UUID = "00000000-0000-4000-8000-000000000001"


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

    dependencies = discover_template_dependencies(registry=registry, host_uuid=UUID)

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

    try:
        discover_template_dependencies(registry=registry, host_uuid=UUID)
    except ValueError as error:
        assert str(error) == "template_dependency_unavailable"
    else:
        raise AssertionError("expected missing template to fail")


def test_rejects_host_reference_outside_registry_hosts_root(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    (registry / "hosts").mkdir(parents=True)

    try:
        discover_template_dependencies(registry=registry, host_uuid="../outside")
    except ValueError as error:
        assert str(error) == "template_dependency_unavailable"
    else:
        raise AssertionError("expected traversal reference to fail")
