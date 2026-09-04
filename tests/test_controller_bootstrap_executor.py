from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_controller_image_bundles_the_host_bootstrap_executor() -> None:
    dockerfile = (ROOT / "Dockerfile.controller").read_text(encoding="utf-8")
    manifest = json.loads(
        (ROOT / "ansible" / "executors" / "infralink-host-baseline.json").read_text(
            encoding="utf-8"
        )
    )

    assert "ansible-core" in dockerfile
    assert "COPY ansible ./ansible" in dockerfile
    assert manifest == {
        "schema_version": "infralink.host-bootstrap-executor/v1",
        "id": "infra-management-host-baseline",
        "playbook": "ansible/playbooks/infralink_host_baseline.yml",
        "allowed_actions": [
            "install_git",
            "install_docker",
            "install_jq",
            "install_bws_cli",
            "install_self_deploy_dependencies",
            "bootstrap_infralink_controller",
        ],
    }
    playbook = ROOT / manifest["playbook"]
    assert playbook.is_file()
    assert "../tasks/infralink_host_baseline.yml" in playbook.read_text(encoding="utf-8")
    assert (ROOT / "ansible" / "tasks" / "infralink_host_baseline.yml").is_file()
