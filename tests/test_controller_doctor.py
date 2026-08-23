from __future__ import annotations

import subprocess
from pathlib import Path

from infralink_ops.controller_doctor import main

UUID = "00000000-0000-4000-8000-000000000001"
REPOSITORY = "ssh://git@registry.example:2222/relaxgg/infra-registry.git"
IMAGE = "ghcr.io/example/controller@sha256:" + ("a" * 64)


def _registry(tmp_path: Path) -> tuple[Path, str]:
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / UUID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("hosts: {}\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q", registry],
        ["git", "-C", registry, "config", "user.email", "tests@example.invalid"],
        ["git", "-C", registry, "config", "user.name", "Tests"],
        ["git", "-C", registry, "add", "."],
        ["git", "-C", registry, "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, check=True)
    revision = subprocess.check_output(
        ["git", "-C", registry, "rev-parse", "HEAD"], text=True
    ).strip()
    return registry, revision


def test_rejects_reconcile_evidence_for_a_different_configured_source(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"INFRALINK_HOST_UUID={UUID}\n"
        f"INFRALINK_CONTROLLER_IMAGE={IMAGE}\n"
        "INFRALINK_REGISTRY_REF=main\n"
        f"INFRALINK_REGISTRY_REPO_URL={REPOSITORY}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "reconcile-result.yml").write_text(
        "status: success\n"
        f"host_uuid: {UUID}\n"
        f"registry_head: {revision}\n"
        "registry_ref: v1\n"
        f"registry_repo_url: {REPOSITORY}\n"
        "controller_reference: ghcr.io/example/controller:main\n"
        f"controller_digest: {IMAGE}\n",
        encoding="utf-8",
    )
    key = tmp_path / "registry-read"
    key.write_text("key\n", encoding="utf-8")

    payload, status = main(
        [
            "--host-env",
            str(host_env),
            "--registry",
            str(registry),
            "--registry-key",
            str(key),
            "--runtime-dir",
            str(runtime),
        ]
    )

    assert status == 78
    assert payload["status"] == "unhealthy"
    assert payload["reason"] == "controller_reconcile_evidence_stale"


def test_reports_healthy_only_with_matching_local_runtime_evidence(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"INFRALINK_HOST_UUID={UUID}\n"
        f"INFRALINK_CONTROLLER_IMAGE={IMAGE}\n"
        "INFRALINK_REGISTRY_REF=main\n"
        f"INFRALINK_REGISTRY_REPO_URL={REPOSITORY}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "reconcile-result.yml").write_text(
        "status: success\n"
        f"host_uuid: {UUID}\n"
        f"registry_head: {revision}\n"
        "registry_ref: main\n"
        f"registry_repo_url: {REPOSITORY}\n"
        "controller_reference: ghcr.io/example/controller:main\n"
        f"controller_digest: {IMAGE}\n",
        encoding="utf-8",
    )
    key = tmp_path / "registry-read"
    key.write_text("key\n", encoding="utf-8")
    services = tmp_path / "services"
    services.mkdir()
    (services / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    textfiles = tmp_path / "textfiles"
    textfiles.mkdir()
    (textfiles / "infralink-controller-reconcile.prom").write_text(
        f'infralink_controller_reconcile_converged{{revision="{revision}"}} 1\n'
        "infralink_controller_reconcile_converged 1\n",
        encoding="utf-8",
    )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *' config --format json') printf '%s\\n' '{\"services\":{}}' ;;\n"
        "  *' ps --all --format json') printf '%s\\n' '[]' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    payload, status = main(
        [
            "--host-env",
            str(host_env),
            "--registry",
            str(registry),
            "--registry-key",
            str(key),
            "--runtime-dir",
            str(runtime),
            "--services-dir",
            str(services),
            "--textfile-directory",
            str(textfiles),
            "--docker",
            str(docker),
        ]
    )

    assert status == 0
    assert payload["status"] == "healthy"
    assert payload["evidence"]["registry"]["head"] == revision


def test_rejects_metric_substrings_that_would_falsely_report_convergence(tmp_path: Path) -> None:
    registry, revision = _registry(tmp_path)
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"INFRALINK_HOST_UUID={UUID}\n"
        f"INFRALINK_CONTROLLER_IMAGE={IMAGE}\n"
        "INFRALINK_REGISTRY_REF=main\n"
        f"INFRALINK_REGISTRY_REPO_URL={REPOSITORY}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "reconcile-result.yml").write_text(
        "status: success\n"
        f"host_uuid: {UUID}\n"
        f"registry_head: {revision}\n"
        "registry_ref: main\n"
        f"registry_repo_url: {REPOSITORY}\n"
        "controller_reference: ghcr.io/example/controller:main\n"
        f"controller_digest: {IMAGE}\n",
        encoding="utf-8",
    )
    key = tmp_path / "registry-read"
    key.write_text("key\n", encoding="utf-8")
    services = tmp_path / "services"
    services.mkdir()
    (services / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    textfiles = tmp_path / "textfiles"
    textfiles.mkdir()
    (textfiles / "infralink-controller-reconcile.prom").write_text(
        f'# infralink_controller_reconcile_converged 1 revision="{revision}"\n'
        "infralink_controller_reconcile_converged 10\n",
        encoding="utf-8",
    )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *' config --format json') printf '%s\\n' '{\"services\":{}}' ;;\n"
        "  *' ps --all --format json') printf '%s\\n' '[]' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    payload, status = main(
        [
            "--host-env",
            str(host_env),
            "--registry",
            str(registry),
            "--registry-key",
            str(key),
            "--runtime-dir",
            str(runtime),
            "--services-dir",
            str(services),
            "--textfile-directory",
            str(textfiles),
            "--docker",
            str(docker),
        ]
    )

    assert status == 78
    assert payload["reason"] == "controller_reconcile_metric_stale"
