from __future__ import annotations

import subprocess
from pathlib import Path

from infralink_ops.controller_doctor import main

UUID = "00000000-0000-4000-8000-000000000001"
REPOSITORY = "ssh://git@registry.example:2222/relaxgg/infra-registry.git"
DIGEST = "sha256:" + ("a" * 64)
IMAGE = "ghcr.io/example/controller@" + DIGEST


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
        f"controller_reference: {IMAGE}\n"
        f"controller_digest: {DIGEST}\n",
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
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    key = tmp_path / "registry-read"
    key.write_text("key\n", encoding="utf-8")
    services = tmp_path / "services"
    services.mkdir()
    (services / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    textfiles = tmp_path / "textfiles"
    textfiles.mkdir()
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"INFRALINK_HOST_UUID={UUID}\n"
        "INFRALINK_REGISTRY_REF=main\n"
        f"INFRALINK_REGISTRY_REPO_URL={REPOSITORY}\n"
        f"INFRALINK_REGISTRY_DIR={registry}\n"
        f"INFRALINK_REGISTRY_KEY_FILE={key}\n"
        f"INFRALINK_RUNTIME_DIR={runtime}\n"
        f"INFRALINK_SERVICES_DIR={services}\n"
        f"INFRALINK_NODE_EXPORTER_TEXTFILE_DIR={textfiles}\n",
        encoding="utf-8",
    )
    (runtime / "reconcile-result.yml").write_text(
        "status: success\n"
        f"host_uuid: {UUID}\n"
        f"registry_head: {revision}\n"
        "registry_ref: main\n"
        f"registry_repo_url: {REPOSITORY}\n"
        f"controller_reference: {IMAGE}\n"
        f"controller_digest: {DIGEST}\n",
        encoding="utf-8",
    )
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
        f"controller_reference: {IMAGE}\n"
        f"controller_digest: {DIGEST}\n",
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


def _healthy_runtime(
    tmp_path: Path, *, compose_state: str = "[]", compose_config: str = '{"services":{}}'
) -> tuple[list[str], Path]:
    """Create a complete local-only controller fixture for state-specific tests."""

    registry, revision = _registry(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    key = tmp_path / "registry-read"
    key.write_text("key\n", encoding="utf-8")
    services = tmp_path / "services"
    services.mkdir()
    (services / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    textfiles = tmp_path / "textfiles"
    textfiles.mkdir()
    (runtime / "reconcile-result.yml").write_text(
        "status: success\n"
        f"host_uuid: {UUID}\n"
        f"registry_head: {revision}\n"
        "registry_ref: main\n"
        f"registry_repo_url: {REPOSITORY}\n"
        f"controller_reference: {IMAGE}\n"
        f"controller_digest: {DIGEST}\n",
        encoding="utf-8",
    )
    (textfiles / "infralink-controller-reconcile.prom").write_text(
        f'infralink_controller_reconcile_converged{{revision="{revision}"}} 1\n'
        "infralink_controller_reconcile_converged 1\n",
        encoding="utf-8",
    )
    host_env = tmp_path / "host.env"
    host_env.write_text(
        f"INFRALINK_HOST_UUID={UUID}\n"
        "INFRALINK_REGISTRY_REF=main\n"
        f"INFRALINK_REGISTRY_REPO_URL={REPOSITORY}\n"
        f"INFRALINK_REGISTRY_DIR={registry}\n"
        f"INFRALINK_REGISTRY_KEY_FILE={key}\n"
        f"INFRALINK_RUNTIME_DIR={runtime}\n"
        f"INFRALINK_SERVICES_DIR={services}\n"
        f"INFRALINK_NODE_EXPORTER_TEXTFILE_DIR={textfiles}\n",
        encoding="utf-8",
    )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f"  *' config --format json') printf '%b\\n' '{compose_config}' ;;\n"
        f"  *' ps --all --format json') printf '%b\\n' '{compose_state}' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return ["--host-env", str(host_env), "--docker", str(docker)], textfiles


def test_accepts_cleanly_exited_one_shot_service_from_line_delimited_compose_state(
    tmp_path: Path,
) -> None:
    arguments, _ = _healthy_runtime(
        tmp_path,
        compose_config='{"services":{"api":{},"setup":{"restart":"no"}}}',
        compose_state=(
            '{"Service":"api","State":"running","ExitCode":0}\\n'
            '{"Service":"setup","State":"exited","ExitCode":0}'
        ),
    )

    payload, status = main(arguments)

    assert status == 0
    assert payload["status"] == "healthy"


def test_rejects_failed_one_shot_service_from_compose_json_array(tmp_path: Path) -> None:
    arguments, _ = _healthy_runtime(
        tmp_path,
        compose_config='{"services":{"setup":{"restart":"no"}}}',
        compose_state='[{"Service":"setup","State":"exited","ExitCode":1}]',
    )

    payload, status = main(arguments)

    assert status == 78
    assert payload["reason"] == "declared_compose_services_not_running"


def test_distinguishes_declared_and_live_compose_unavailability(tmp_path: Path) -> None:
    arguments, _ = _healthy_runtime(tmp_path)
    docker = Path(arguments[-1])
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    payload, status = main(arguments)

    assert status == 78
    assert payload["reason"] == "declared_compose_unavailable"

    arguments, _ = _healthy_runtime(tmp_path / "live")
    docker = Path(arguments[-1])
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *' config --format json') printf '%s\\n' '{\"services\":{}}' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    payload, status = main(arguments)

    assert status == 78
    assert payload["reason"] == "live_compose_unavailable"


def test_rejects_missing_node_exporter_textfile_directory(tmp_path: Path) -> None:
    arguments, textfiles = _healthy_runtime(tmp_path)
    (textfiles / "infralink-controller-reconcile.prom").unlink()
    textfiles.rmdir()

    payload, status = main(arguments)

    assert status == 78
    assert payload["reason"] == "node_exporter_textfile_directory_missing"


def test_reports_declared_firewall_runtime_drift(tmp_path: Path, monkeypatch: object) -> None:
    arguments, _ = _healthy_runtime(tmp_path)
    host_env = Path(arguments[1])
    registry = Path(
        next(
            line.partition("=")[2]
            for line in host_env.read_text(encoding="utf-8").splitlines()
            if line.startswith("INFRALINK_REGISTRY_DIR=")
        )
    )
    deployment = registry / "hosts" / UUID / "operations" / "deployment.yml"
    deployment.parent.mkdir()
    deployment.write_text(
        "firewall:\n"
        "  backend: nftables\n"
        "  mode: default-deny\n"
        "  management_ssh:\n"
        "    port: 22\n"
        "    interface: tailscale0\n"
        "    sources: [100.64.0.0/10]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", registry, "add", "."], check=True)
    subprocess.run(["git", "-C", registry, "commit", "-qm", "firewall"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", registry, "rev-parse", "HEAD"], text=True
    ).strip()
    runtime = Path(
        next(
            line.partition("=")[2]
            for line in host_env.read_text(encoding="utf-8").splitlines()
            if line.startswith("INFRALINK_RUNTIME_DIR=")
        )
    )
    evidence = runtime / "reconcile-result.yml"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            subprocess.check_output(
                ["git", "-C", registry, "rev-parse", "HEAD~"], text=True
            ).strip(),
            revision,
        ),
        encoding="utf-8",
    )
    textfiles = Path(
        next(
            line.partition("=")[2]
            for line in host_env.read_text(encoding="utf-8").splitlines()
            if line.startswith("INFRALINK_NODE_EXPORTER_TEXTFILE_DIR=")
        )
    )
    metric = textfiles / "infralink-controller-reconcile.prom"
    metric.write_text(
        metric.read_text(encoding="utf-8").replace(
            subprocess.check_output(
                ["git", "-C", registry, "rev-parse", "HEAD~"], text=True
            ).strip(),
            revision,
        ),
        encoding="utf-8",
    )

    from infralink_ops import controller_doctor

    calls: list[object] = []

    def raise_drift(**kwargs: object) -> None:
        calls.append(kwargs)
        raise controller_doctor.FirewallError("firewall_runtime_drift")

    monkeypatch.setattr(controller_doctor, "verify_firewall_policy", raise_drift)
    payload, status = main(arguments)

    assert status == 78
    assert len(calls) == 1
    assert payload["reason"] == "declared_firewall_runtime_drift"
