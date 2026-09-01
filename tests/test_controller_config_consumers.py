from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


def test_validate_selects_affected_declared_consumer(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text(
        yaml.safe_dump(
            {
                "rendered_config_consumers": [
                    {
                        "id": "nginx",
                        "path_prefix": "nginx",
                        "service": "nginx",
                        "validation_argv": ["nginx", "-t"],
                        "lifecycle": "compose-recreate",
                    },
                    {
                        "id": "unaffected",
                        "path_prefix": "other",
                        "service": "other",
                        "validation_argv": ["true"],
                        "lifecycle": "compose-recreate",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {nginx: {}, other: {}}\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_root.mkdir()
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "validate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            '["nginx/nginx.conf"]',
        ]
    )

    assert status == 0
    assert payload == {
        "schema_version": "infralink.ops.config-consumers/v1",
        "ok": True,
        "command": {
            "path": ["validate"],
            "args": {
                "deployment": str(deployment),
                "compose": str(compose),
                "config_root": str(config_root),
                "changed_paths": ["nginx/nginx.conf"],
            },
        },
        "result": {"consumers": ["nginx"], "services": ["nginx"]},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {compose} run --rm --no-deps nginx nginx -t"
    ]


def test_activate_recreates_changed_direct_file_bind(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_path = config_root / "nginx" / "nginx.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("events {}\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "nginx": {
                        "volumes": [
                            f"{config_path}:/etc/nginx/nginx.conf:ro",
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            '["nginx/nginx.conf"]',
        ]
    )

    assert status == 0
    assert payload["result"] == {"consumers": [], "services": ["nginx"]}
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {compose} up -d --no-deps --force-recreate nginx"
    ]


def test_activate_rejects_missing_managed_direct_file_bind(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_root.mkdir()
    missing_path = config_root / "nginx" / "nginx.conf"
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "nginx": {
                        "volumes": [
                            f"{missing_path}:/etc/nginx/nginx.conf:ro",
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            "[]",
        ]
    )

    assert status == 78
    assert payload["error"] == {"code": "config_consumers_failed"}
    assert not log.exists()


def test_activate_recreates_stale_direct_file_bind(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_path = config_root / "nginx" / "nginx.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("events {}\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "nginx": {
                        "volumes": [
                            f"{config_path}:/etc/nginx/nginx.conf:ro",
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        "if [[ \"$*\" == *' ps -q nginx'* ]]; then printf '%s\\n' container-id; fi\n"
        'if [[ "$1" == cp ]]; then printf \'%s\\n\' stale > "${!#}"; fi\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            "[]",
        ]
    )

    assert status == 0
    assert payload["result"] == {"consumers": [], "services": ["nginx"]}
    lines = log.read_text(encoding="utf-8").splitlines()
    assert f"compose -f {compose} ps -q nginx" in lines
    assert "cp container-id:/etc/nginx/nginx.conf" in "\n".join(lines)
    assert f"compose -f {compose} up -d --no-deps --force-recreate nginx" in lines


def test_activate_ignores_exited_direct_file_bind_consumer(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_path = config_root / "postgres" / "provision.sh"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("#!/bin/sh\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "postgresql-provisioner": {
                        "volumes": [
                            f"{config_path}:/usr/local/bin/provision:ro",
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            "[]",
        ]
    )

    assert status == 0
    assert payload["result"] == {"consumers": [], "services": []}
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"compose -f {compose} ps -q postgresql-provisioner"
    ]

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            '["postgres/provision.sh"]',
        ]
    )

    assert status == 0
    assert payload["result"] == {
        "consumers": [],
        "services": ["postgresql-provisioner"],
    }
    assert log.read_text(encoding="utf-8").splitlines()[-1] == (
        f"compose -f {compose} up -d --no-deps --force-recreate postgresql-provisioner"
    )


def test_rejects_malformed_consumer_declaration(tmp_path: Path) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text(
        yaml.safe_dump(
            {
                "rendered_config_consumers": [
                    {
                        "id": "nginx",
                        "path_prefix": "nginx",
                        "service": "nginx",
                        "validation_argv": ["nginx", "-t"],
                        "lifecycle": "shell-restart",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_root.mkdir()

    payload, status = main(
        [
            "validate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            '["nginx/nginx.conf"]',
        ]
    )

    assert status == 78
    assert payload["ok"] is False
    assert payload["error"] == {"code": "config_consumers_failed"}


def test_rejects_relative_config_root_before_docker(tmp_path: Path, monkeypatch) -> None:
    from infralink_ops.controller_config_consumers import main

    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))

    payload, status = main(
        [
            "activate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            "relative-config",
            "--changed-paths-json",
            "[]",
        ]
    )

    assert status == 78
    assert payload["error"] == {"code": "config_consumers_failed"}
    assert not log.exists()


def test_module_cli_emits_yaml_envelope_and_usage_error(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment.yml"
    deployment.write_text("rendered_config_consumers: []\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    config_root = tmp_path / "config"
    config_root.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_config_consumers",
            "validate",
            "--deployment",
            str(deployment),
            "--compose",
            str(compose),
            "--config-root",
            str(config_root),
            "--changed-paths-json",
            "[]",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    usage = subprocess.run(
        [sys.executable, "-m", "infralink_ops.controller_config_consumers"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert yaml.safe_load(completed.stdout)["schema_version"] == "infralink.ops.config-consumers/v1"
    assert usage.returncode == 64
    assert yaml.safe_load(usage.stdout)["error"] == {"code": "usage_error"}
