from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

MODULE = "infralink_ops.controller_reconcile_evidence"
REVISION = "a" * 40
HOST_UUID = "11111111-1111-4111-8111-111111111111"
OBSERVED_AT = "2026-08-25T06:00:00Z"
ADAPTER_JSON = (
    '{"schema_version":"infralink.controller-adapter-result/v1","phase":"apply",'
    '"status":"applied","registry_revision":"' + REVISION + '",'
    '"actions":[{"category":"service","state":"changed","count":1}],'
    '"evidence":[{"kind":"service","status":"passed"}]}'
)


def run_evidence(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def test_write_success_records_revision_and_publishes_metrics(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-success",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--registry-revision",
        REVISION,
        "--registry-ref",
        "main",
        "--registry-repo-url",
        "ssh://git@gitea.example/relaxgg/infra-registry.git",
        "--controller-reference",
        "ghcr.io/example/controller@sha256:" + ("b" * 64),
        "--controller-digest",
        "sha256:" + ("b" * 64),
        "--adapter-json",
        ADAPTER_JSON,
        "--observed-at",
        OBSERVED_AT,
        "--docker-image-cleanup-json",
        '{"status":"ok"}',
        "--docker-image-cleanup-json",
        '{"status":"warning","reason":"docker_image_prune_failed"}',
    )

    assert completed.returncode == 0, completed.stderr
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["schema_version"] == "infralink.ops.controller-reconcile-evidence/v1"
    assert envelope["ok"] is True
    assert envelope["result"]["status"] == "success"

    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record["schema_version"] == "infralink.controller-reconcile/v2"
    assert record["status"] == "success"
    assert record["host_uuid"] == HOST_UUID
    assert record["registry_head"] == REVISION
    assert record["adapter"] == yaml.safe_load(ADAPTER_JSON)
    assert record["docker_image_cleanup"] == {
        "status": "warning",
        "reason": "docker_image_prune_failed",
    }

    metrics = (textfile_dir / "infralink-controller-reconcile.prom").read_text()
    assert "infralink_controller_reconcile_converged 1" in metrics
    assert f'revision="{REVISION}"' in metrics


def test_write_failure_replaces_stale_success_with_a_bounded_reason(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()
    (runtime_root / "reconcile-result.yml").write_text("status: success\n", encoding="utf-8")
    (textfile_dir / "infralink-controller-reconcile.prom").write_text(
        "infralink_controller_reconcile_converged 1\n", encoding="ascii"
    )

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "registry_checkout_failed",
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record == {
        "schema_version": "infralink.controller-reconcile/v2",
        "status": "failure",
        "host_uuid": HOST_UUID,
        "reason_code": "registry_checkout_failed",
        "observed_at": OBSERVED_AT,
    }
    metrics = (textfile_dir / "infralink-controller-reconcile.prom").read_text()
    assert "infralink_controller_reconcile_converged 0" in metrics
    assert "last_success_timestamp_seconds" not in metrics
    assert "registry_checkout_failed" not in metrics


def test_write_failure_records_a_strict_adapter_failure_summary(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "template_render_failed",
        "--failure-details-json",
        '{"stage":"adapter","exit_code":78,"diagnostic_code":"template_render_failed"}',
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record["failure"] == {
        "stage": "adapter",
        "exit_code": 78,
        "diagnostic_code": "template_render_failed",
    }


def test_write_failure_records_a_bounded_native_runtime_stage(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "compose_validation_failed",
        "--failure-details-json",
        '{"stage":"compose_validation","exit_code":1,"diagnostic_code":"compose_validation_failed"}',
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record["failure"] == {
        "stage": "compose_validation",
        "exit_code": 1,
        "diagnostic_code": "compose_validation_failed",
    }


def test_write_failure_records_bounded_management_interface_evidence(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "firewall_management_interface_missing",
        "--failure-details-json",
        '{"declared":"eth0","observed":["enp6s0","lo"],"observed_count":2}',
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record["failure"] == {
        "declared": "eth0",
        "observed": ["enp6s0", "lo"],
        "observed_count": 2,
    }


def test_write_failure_records_bounded_ingress_listener_evidence(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "firewall_ingress_bind_address_missing",
        "--failure-details-json",
        (
            '{"service":"web","interface":"enp6s0",'
            '"bind_address":"203.0.113.10","observed":[],"observed_count":0}'
        ),
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    record = yaml.safe_load((runtime_root / "reconcile-result.yml").read_text())
    assert record["failure"] == {
        "service": "web",
        "interface": "enp6s0",
        "bind_address": "203.0.113.10",
        "observed": [],
        "observed_count": 0,
    }


def test_write_failure_rejects_raw_adapter_stderr_without_mutating_evidence(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()
    evidence_path = runtime_root / "reconcile-result.yml"
    evidence_path.write_text("status: success\n", encoding="utf-8")

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "controller_adapter_failed",
        "--failure-details-json",
        '{"stage":"adapter","exit_code":78,"stderr":"secret-like-value"}',
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 64
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["error"] == {"code": "failure_details_invalid"}
    assert evidence_path.read_text(encoding="utf-8") == "status: success\n"


def test_write_failure_rejects_unbounded_reason_without_mutating_evidence(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()
    evidence_path = runtime_root / "reconcile-result.yml"
    metrics_path = textfile_dir / "infralink-controller-reconcile.prom"
    evidence_path.write_text("status: success\n", encoding="utf-8")
    metrics_path.write_text("infralink_controller_reconcile_converged 1\n", encoding="ascii")

    completed = run_evidence(
        "write-failure",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--reason-code",
        "docker exited 125: unauthorized",
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 64
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["error"] == {"code": "reason_code_invalid"}
    assert evidence_path.read_text(encoding="utf-8") == "status: success\n"
    assert (
        metrics_path.read_text(encoding="ascii") == "infralink_controller_reconcile_converged 1\n"
    )


def test_write_success_rejects_an_invalid_timestamp_before_writing(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-success",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--registry-revision",
        REVISION,
        "--registry-ref",
        "main",
        "--registry-repo-url",
        "ssh://git@gitea.example/relaxgg/infra-registry.git",
        "--controller-reference",
        "ghcr.io/example/controller@sha256:" + ("b" * 64),
        "--controller-digest",
        "sha256:" + ("b" * 64),
        "--adapter-json",
        ADAPTER_JSON,
        "--observed-at",
        "not-a-timestamp",
        "--docker-image-cleanup-json",
        '{"status":"ok"}',
    )

    assert completed.returncode == 64
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["error"] == {"code": "observed_at_invalid"}
    assert not (runtime_root / "reconcile-result.yml").exists()
    assert not (textfile_dir / "infralink-controller-reconcile.prom").exists()


def test_write_success_rejects_unknown_adapter_fields_without_writing(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()
    adapter_json = ADAPTER_JSON[:-1] + ',"stderr":"secret-like-value"}'

    completed = run_evidence(
        "write-success",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--registry-revision",
        REVISION,
        "--registry-ref",
        "main",
        "--registry-repo-url",
        "ssh://git@gitea.example/relaxgg/infra-registry.git",
        "--controller-reference",
        "ghcr.io/example/controller@sha256:" + ("b" * 64),
        "--controller-digest",
        "sha256:" + ("b" * 64),
        "--adapter-json",
        adapter_json,
        "--observed-at",
        OBSERVED_AT,
        "--docker-image-cleanup-json",
        '{"status":"ok"}',
    )

    assert completed.returncode == 64
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["error"] == {"code": "adapter_invalid"}
    assert not (runtime_root / "reconcile-result.yml").exists()
    assert not (textfile_dir / "infralink-controller-reconcile.prom").exists()


def test_write_success_rejects_unbounded_cache_evidence_without_writing(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    textfile_dir = tmp_path / "textfiles"
    runtime_root.mkdir()
    textfile_dir.mkdir()

    completed = run_evidence(
        "write-success",
        "--runtime-root",
        str(runtime_root),
        "--textfile-directory",
        str(textfile_dir),
        "--host-uuid",
        HOST_UUID,
        "--registry-revision",
        REVISION,
        "--registry-ref",
        "main",
        "--registry-repo-url",
        "ssh://git@gitea.example/relaxgg/infra-registry.git",
        "--controller-reference",
        "ghcr.io/example/controller@sha256:" + ("b" * 64),
        "--controller-digest",
        "sha256:" + ("b" * 64),
        "--adapter-json",
        ADAPTER_JSON,
        "--observed-at",
        OBSERVED_AT,
        "--docker-image-cleanup-json",
        '{"status":"warning","stderr":"secret-like-value"}',
    )

    assert completed.returncode == 64
    envelope = yaml.safe_load(completed.stdout)
    assert envelope["error"] == {"code": "docker_image_cleanup_invalid"}
    assert not (runtime_root / "reconcile-result.yml").exists()
    assert not (textfile_dir / "infralink-controller-reconcile.prom").exists()
