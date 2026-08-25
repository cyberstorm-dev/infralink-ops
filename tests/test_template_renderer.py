import os
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

from infralink_ops.template_renderer import (
    TemplateRenderError,
    load_host_configuration_bindings,
    register_generic_jinja_helpers,
    render_declared_host,
)


def _host_manifest(uuid: str) -> str:
    return f"""hosts:
  {uuid}:
    canonical_name: neutral-host
    rendered_config_permissions:
      - path: app/settings.yml
        mode: \"0640\"
        owner_uid: {os.getuid()}
        owner_gid: {os.getgid()}
"""


def _registry(tmp_path: Path) -> tuple[Path, str]:
    uuid = "11111111-1111-1111-1111-111111111111"
    registry = tmp_path / "registry"
    host = registry / "hosts" / uuid
    (host / "partials").mkdir(parents=True)
    (host / "config" / "app").mkdir(parents=True)
    (host / "manifest.yml").write_text(_host_manifest(uuid), encoding="utf-8")
    (host / "partials" / "image.j2").write_text("{{ images.app }}\n", encoding="utf-8")
    (host / "docker-compose.yml.j2").write_text(
        "services:\n  app:\n    image: {% include 'partials/image.j2' %}", encoding="utf-8"
    )
    (host / "config" / "app" / "settings.yml.j2").write_text(
        "host: {{ canonical_name }}\nport: {{ port }}\n", encoding="utf-8"
    )
    return registry, uuid


def _commit_registry(registry: Path) -> str:
    subprocess.run(["git", "init", "-q", str(registry)], check=True)
    subprocess.run(
        ["git", "-C", str(registry), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(registry), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(registry), "add", "."], check=True)
    subprocess.run(["git", "-C", str(registry), "commit", "-qm", "initial"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(registry), "rev-parse", "HEAD"], text=True
    ).strip()


def test_renders_declared_host_with_relative_include_permissions_and_stale_prune(
    tmp_path: Path,
) -> None:
    registry, uuid = _registry(tmp_path)
    services = tmp_path / "services"
    stale = services / "config" / "retired.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("retired\n", encoding="utf-8")
    (services / ".infralink-managed-config.json").write_text('["retired.yml"]', encoding="utf-8")

    result = render_declared_host(
        registry=registry,
        host_id=uuid,
        services_dir=services,
        resolved_images={"app": "ghcr.io/example/app@sha256:" + "a" * 64},
        context={"port": 8080, "canonical_name": "must-not-override-host"},
    )

    assert (services / "docker-compose.yml").read_text(encoding="utf-8") == (
        "services:\n  app:\n    image: ghcr.io/example/app@sha256:" + "a" * 64 + "\n"
    )
    settings = services / "config" / "app" / "settings.yml"
    assert settings.read_text(encoding="utf-8") == "host: neutral-host\nport: 8080\n"
    assert settings.stat().st_mode & 0o777 == 0o640
    assert not stale.exists()
    assert set(result.changed_config_paths) == {"app/settings.yml", "retired.yml"}


def test_rejects_non_immutable_resolved_image_before_writing(tmp_path: Path) -> None:
    registry, uuid = _registry(tmp_path)
    services = tmp_path / "services"

    with pytest.raises(TemplateRenderError, match="resolved image map is invalid"):
        render_declared_host(
            registry=registry,
            host_id=uuid,
            services_dir=services,
            resolved_images={"app": "ghcr.io/example/app:latest"},
        )

    assert not services.exists()


def test_projects_host_configuration_bindings_by_profile_and_slot(tmp_path: Path) -> None:
    registry, uuid = _registry(tmp_path)
    catalog = registry / "service-catalog" / "v2"
    catalog.mkdir(parents=True)
    (catalog / "profiles.yml").write_text(
        f"""schema_version: infralink.observation/v2
service_profiles:
  - id: tenant-stack
    components: [{{id: worker, endpoints: []}}]
    configuration_slots:
      - id: tenant
        kind: record
        purpose: Declare one tenant stack.
        fields:
          - {{id: id, kind: string}}
          - {{id: hosts, kind: string-list-map}}
service_instances:
  - id: tenant-a
    host_id: {uuid}
    profile_id: tenant-stack
    components: [{{slot_id: worker}}]
    configuration_bindings:
      - slot_id: tenant
        value:
          id: a
          hosts: {{irc: [irc.a.example.test]}}
  - id: tenant-b
    host_id: {uuid}
    profile_id: tenant-stack
    components: [{{slot_id: worker}}]
    configuration_bindings:
      - slot_id: tenant
        value:
          id: b
          hosts: {{irc: [irc.b.example.test]}}
""",
        encoding="ascii",
    )

    configuration = load_host_configuration_bindings(registry=registry, host_id=uuid)

    assert configuration == {
        "tenant-stack": {
            "tenant": [
                {
                    "component_id": None,
                    "service_instance_id": "tenant-a",
                    "value": {"id": "a", "hosts": {"irc": ["irc.a.example.test"]}},
                },
                {
                    "component_id": None,
                    "service_instance_id": "tenant-b",
                    "value": {"id": "b", "hosts": {"irc": ["irc.b.example.test"]}},
                },
            ]
        }
    }

    (registry / "hosts" / uuid / "docker-compose.yml.j2").write_text(
        "tenant: {{ configuration['tenant-stack']['tenant'][0]['value']['id'] }}\n",
        encoding="ascii",
    )
    render_declared_host(
        registry=registry,
        host_id=uuid,
        services_dir=tmp_path / "services",
        resolved_images={"app": "ghcr.io/example/app@sha256:" + "a" * 64},
        context={"port": 8080},
    )
    assert (tmp_path / "services" / "docker-compose.yml").read_text(
        encoding="ascii"
    ) == "tenant: a\n"


def test_generic_jinja_helpers_preserve_dsn_and_nginx_contracts() -> None:
    env = Environment()
    register_generic_jinja_helpers(env)

    dsn = "mysql://editor:Ts8BtKlfx/hSTXtRF16JouhuLepqpEbt@100.89.135.65:3306/relayos?x=1"
    rendered = env.from_string(
        "{{ dsn | dsn_host }}|{{ dsn | dsn_port }}|{{ dsn | dsn_database }}|"
        "{{ dsn | dsn_username }}|{{ dsn | dsn_password }}|"
        "{{ dsn | dsn_with_database('tenant') }}"
    ).render(dsn=dsn)

    assert rendered == (
        "100.89.135.65|3306|relayos|editor|Ts8BtKlfx/hSTXtRF16JouhuLepqpEbt|"
        "mysql://editor:Ts8BtKlfx/hSTXtRF16JouhuLepqpEbt@100.89.135.65:3306/tenant?x=1"
    )
    assert env.from_string("{{ value | nginx_quoted }}").render(value='x"y\\z') == '"x\\"y\\\\z"'


def test_declared_template_source_renders_literal_and_jinja_with_host_context(
    tmp_path: Path,
) -> None:
    registry, uuid = _registry(tmp_path)
    source = registry / "shared" / "application-config"
    source.mkdir(parents=True)
    (source / "literal.conf").write_text("literal = yes\n", encoding="ascii")
    (source / "rendered.conf.j2").write_text("host = {{ canonical_name }}\n", encoding="ascii")
    manifest = registry / "hosts" / uuid / "manifest.yml"
    manifest.write_text(
        _host_manifest(uuid)
        + "    template_sources:\n"
        + "      - id: application-config\n"
        + "        source: shared/application-config\n",
        encoding="ascii",
    )
    (registry / "hosts" / uuid / "docker-compose.yml.j2").write_text(
        "{% include 'sources/application-config/literal.conf' %}"
        "{% include 'sources/application-config/rendered.conf.j2' %}",
        encoding="ascii",
    )

    revision = _commit_registry(registry)
    result = render_declared_host(
        registry=registry,
        host_id=uuid,
        services_dir=tmp_path / "services",
        resolved_images={"app": "ghcr.io/example/app@sha256:" + "a" * 64},
        expected_registry_revision=revision,
        context={"port": 8080},
    )

    assert result.compose_changed is True
    assert (tmp_path / "services" / "docker-compose.yml").read_text(encoding="ascii") == (
        "literal = yes\nhost = neutral-host\n"
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "      - id: ../escape\n        source: shared/application-config\n",
        "      - id: application-config\n        source: ../escape\n",
        "      - id: application-config\n        source: shared/application-config\n"
        "      - id: application-config\n        source: shared/other\n",
    ],
)
def test_rejects_invalid_declared_template_source_before_writing(
    tmp_path: Path, declaration: str
) -> None:
    registry, uuid = _registry(tmp_path)
    (registry / "shared" / "application-config").mkdir(parents=True)
    manifest = registry / "hosts" / uuid / "manifest.yml"
    manifest.write_text(
        _host_manifest(uuid) + "    template_sources:\n" + declaration, encoding="ascii"
    )
    revision = _commit_registry(registry)

    with pytest.raises(TemplateRenderError, match="template source"):
        render_declared_host(
            registry=registry,
            host_id=uuid,
            services_dir=tmp_path / "services",
            resolved_images={"app": "ghcr.io/example/app@sha256:" + "a" * 64},
            expected_registry_revision=revision,
        )

    assert not (tmp_path / "services").exists()


def test_rejects_symlink_in_declared_template_source_before_writing(tmp_path: Path) -> None:
    registry, uuid = _registry(tmp_path)
    source = registry / "shared" / "application-config"
    source.mkdir(parents=True)
    (source / "base.conf").symlink_to("/etc/passwd")
    manifest = registry / "hosts" / uuid / "manifest.yml"
    manifest.write_text(
        _host_manifest(uuid)
        + "    template_sources:\n"
        + "      - id: application-config\n"
        + "        source: shared/application-config\n",
        encoding="ascii",
    )
    revision = _commit_registry(registry)

    with pytest.raises(TemplateRenderError, match="template source"):
        render_declared_host(
            registry=registry,
            host_id=uuid,
            services_dir=tmp_path / "services",
            resolved_images={"app": "ghcr.io/example/app@sha256:" + "a" * 64},
            expected_registry_revision=revision,
        )

    assert not (tmp_path / "services").exists()
