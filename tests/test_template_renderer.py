import os
from pathlib import Path

import pytest
from jinja2 import Environment

from infralink_ops.template_renderer import (
    TemplateRenderError,
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
