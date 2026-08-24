from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def test_lists_deduplicated_absolute_bind_mounts_from_short_and_long_syntax(tmp_path: Path) -> None:
    from infralink_ops.compose_binds import ComposeBindMount, parse_compose_bind_mounts

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "first": {
                        "volumes": [
                            "/opt/services/config/nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
                            "named-volume:/var/lib/app",
                        ]
                    },
                    "second": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": "/opt/services/config/nginx/nginx.conf",
                                "target": "/etc/nginx/nginx.conf",
                            },
                            {
                                "type": "bind",
                                "source": "/var/lib/application",
                                "target": "/var/lib/application",
                            },
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    assert parse_compose_bind_mounts(compose) == (
        ComposeBindMount(
            source="/opt/services/config/nginx/nginx.conf",
            target="/etc/nginx/nginx.conf",
        ),
        ComposeBindMount(source="/var/lib/application", target="/var/lib/application"),
    )


def test_accepts_short_bind_syntax_without_an_access_mode(tmp_path: Path) -> None:
    from infralink_ops.compose_binds import ComposeBindMount, parse_compose_bind_mounts

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  app:\n    volumes:\n      - /var/lib/application:/var/lib/application\n",
        encoding="utf-8",
    )

    assert parse_compose_bind_mounts(compose) == (
        ComposeBindMount(source="/var/lib/application", target="/var/lib/application"),
    )


@pytest.mark.parametrize(
    "document",
    (
        {"services": []},
        {"services": {"app": {"volumes": "not-a-list"}}},
        {"services": {"app": {"volumes": [{"type": "bind", "source": "/host"}]}}},
        {
            "services": {
                "app": {"volumes": [{"type": "bind", "source": "relative", "target": "/run/app"}]}
            }
        },
    ),
)
def test_rejects_malformed_declared_bind_mounts(
    tmp_path: Path, document: dict[str, object]
) -> None:
    from infralink_ops.compose_binds import ComposeBindError, parse_compose_bind_mounts

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ComposeBindError, match="compose_bind_invalid"):
        parse_compose_bind_mounts(compose)
