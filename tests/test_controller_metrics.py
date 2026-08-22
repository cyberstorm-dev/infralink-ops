from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from infralink_ops.controller_metrics import MetricsError, atomic_write

MODULE = "infralink_ops.controller_metrics"
REVISION = "a" * 40
OBSERVED_AT = "2026-08-22T12:00:00Z"


def run_metrics(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def test_publish_success_writes_atomic_prometheus_evidence(tmp_path: Path) -> None:
    output = tmp_path / "textfiles" / "infralink-controller-reconcile.prom"
    output.parent.mkdir()
    output.write_text("old\n", encoding="ascii")

    completed = run_metrics(
        "publish-success",
        "--output",
        str(output),
        "--registry-revision",
        REVISION,
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 0, completed.stderr
    payload = yaml.safe_load(completed.stdout)
    assert payload["schema_version"] == "infralink.ops.controller-metrics/v1"
    assert payload["ok"] is True
    assert payload["command"]["path"] == ["publish-success"]
    assert payload["result"] == {"output": str(output), "status": "success"}
    text = output.read_text(encoding="ascii")
    assert "infralink_controller_reconcile_converged 1" in text
    assert f'revision="{REVISION}"' in text
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_publish_failure_replaces_success_evidence(tmp_path: Path) -> None:
    output = tmp_path / "infralink-controller-reconcile.prom"
    success = run_metrics(
        "publish-success",
        "--output",
        str(output),
        "--registry-revision",
        REVISION,
        "--observed-at",
        OBSERVED_AT,
    )
    assert success.returncode == 0

    completed = run_metrics("publish-failure", "--output", str(output))

    assert completed.returncode == 0, completed.stderr
    payload = yaml.safe_load(completed.stdout)
    assert payload["ok"] is True
    assert payload["result"] == {"output": str(output), "status": "failure"}
    text = output.read_text(encoding="ascii")
    assert "infralink_controller_reconcile_converged 0" in text
    assert "last_success_timestamp_seconds" not in text


def test_publish_success_rejects_an_invalid_registry_revision_without_writing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "infralink-controller-reconcile.prom"

    completed = run_metrics(
        "publish-success",
        "--output",
        str(output),
        "--registry-revision",
        "not-a-sha",
        "--observed-at",
        OBSERVED_AT,
    )

    assert completed.returncode == 64
    payload = yaml.safe_load(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "registry_revision_invalid"
    assert not output.exists()


def test_publish_failure_rejects_a_missing_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "missing" / "infralink-controller-reconcile.prom"

    completed = run_metrics("publish-failure", "--output", str(output))

    assert completed.returncode == 78
    payload = yaml.safe_load(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "output_directory_missing"


def test_publish_failure_returns_yaml_for_a_directory_output_target(tmp_path: Path) -> None:
    output = tmp_path / "output-directory"
    output.mkdir()

    completed = run_metrics("publish-failure", "--output", str(output))

    assert completed.returncode == 78
    assert completed.stderr == ""
    payload = yaml.safe_load(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "output_write_failed"


def test_atomic_write_preserves_prior_target_and_cleans_staging_on_replace_failure(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "infralink-controller-reconcile.prom"
    output.write_text("prior\n", encoding="ascii")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(MetricsError, match="output_write_failed"):
        atomic_write(output, b"replacement\n")

    assert output.read_text(encoding="ascii") == "prior\n"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_atomic_write_cleans_staging_on_fsync_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "infralink-controller-reconcile.prom"

    def fail_fsync(descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(MetricsError, match="output_write_failed"):
        atomic_write(output, b"replacement\n")

    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_missing_command_returns_a_yaml_usage_error() -> None:
    completed = run_metrics()

    assert completed.returncode == 64
    assert completed.stderr == ""
    payload = yaml.safe_load(completed.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "usage_error"
