from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def _deployment(path: Path, directories: list[dict[str, object]]) -> Path:
    path.write_text(yaml.safe_dump({"runtime_directories": directories}), encoding="utf-8")
    return path


def _directory(path: str) -> dict[str, object]:
    return {"path": path, "mode": "0750", "owner_uid": os.getuid(), "owner_gid": os.getgid()}


def test_plan_reports_missing_declared_runtime_directory_without_writing(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/var/lib/node-exporter")])
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = main(["plan", "--deployment", str(deployment), "--host-root", str(host_root)])

    assert status == 0
    assert payload["schema_version"] == "infralink.ops.runtime-directories/v1"
    assert payload["ok"] is True
    assert payload["result"] == {
        "directories": [
            {
                "path": "/var/lib/node-exporter",
                "mode": "0750",
                "owner_uid": os.getuid(),
                "owner_gid": os.getgid(),
                "exists": False,
            }
        ]
    }
    assert not (host_root / "var/lib/node-exporter").exists()


def test_apply_materializes_declared_runtime_directory_with_exact_mode(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/var/lib/node-exporter")])
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = main(
        ["apply", "--deployment", str(deployment), "--host-root", str(host_root)]
    )

    destination = host_root / "var/lib/node-exporter"
    assert status == 0
    assert payload["result"]["directories"][0]["exists"] is True
    assert stat.S_IMODE(destination.stat().st_mode) == 0o750
    assert destination.stat().st_uid == os.getuid()
    assert destination.stat().st_gid == os.getgid()


def test_apply_materializes_declared_service_runtime_directory(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/opt/services/redis/data")])
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = main(
        ["apply", "--deployment", str(deployment), "--host-root", str(host_root)]
    )

    destination = host_root / "opt/services/redis/data"
    assert status == 0
    assert payload["result"]["directories"][0]["path"] == "/opt/services/redis/data"
    assert destination.is_dir()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o750


def test_rejects_runtime_directory_outside_allowed_host_roots(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/etc/infralink")])
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = main(["plan", "--deployment", str(deployment), "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "runtime_directory_path_not_allowed"}


@pytest.mark.parametrize("path", ("/opt/services/.", "/opt/services/..", "/opt/services/redis/.."))
def test_rejects_noncanonical_service_runtime_directory_without_writing(
    tmp_path: Path, path: str
) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory(path)])
    host_root = tmp_path / "host"
    services_root = host_root / "opt/services"
    services_root.mkdir(parents=True)
    original_mode = stat.S_IMODE(services_root.stat().st_mode)

    payload, status = main(
        ["apply", "--deployment", str(deployment), "--host-root", str(host_root)]
    )

    assert status == 78
    assert payload["error"] == {"code": "runtime_directory_path_not_allowed"}
    assert stat.S_IMODE(services_root.stat().st_mode) == original_mode


def test_rejects_symlinked_runtime_directory_ancestor(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/var/lib/node-exporter")])
    host_root = tmp_path / "host"
    host_root.mkdir()
    (host_root / "var").symlink_to(tmp_path / "outside", target_is_directory=True)

    payload, status = main(["plan", "--deployment", str(deployment), "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "runtime_directory_symlink_unsafe"}


def test_apply_preflights_all_directories_before_writing_any(tmp_path: Path) -> None:
    from infralink_ops.controller_runtime_directories import main

    deployment = _deployment(
        tmp_path / "deployment.yml",
        [_directory("/var/lib/node-exporter"), _directory("/var/log/controller")],
    )
    host_root = tmp_path / "host"
    host_root.mkdir()
    (host_root / "var/log").mkdir(parents=True)
    (host_root / "var/log/controller").symlink_to(tmp_path / "outside", target_is_directory=True)

    payload, status = main(
        ["apply", "--deployment", str(deployment), "--host-root", str(host_root)]
    )

    assert status == 78
    assert payload["error"] == {"code": "runtime_directory_symlink_unsafe"}
    assert not (host_root / "var/lib/node-exporter").exists()


def test_apply_maps_filesystem_mutation_failure_to_typed_error(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_runtime_directories as runtime_directories

    deployment = _deployment(tmp_path / "deployment.yml", [_directory("/var/lib/node-exporter")])
    host_root = tmp_path / "host"
    host_root.mkdir()

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(runtime_directories.os, "chmod", denied)

    payload, status = runtime_directories.main(
        ["apply", "--deployment", str(deployment), "--host-root", str(host_root)]
    )

    assert status == 78
    assert payload["error"] == {"code": "runtime_directory_apply_failed"}


def test_module_emits_yaml_usage_envelope(tmp_path: Path) -> None:
    deployment = _deployment(tmp_path / "deployment.yml", [])

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_runtime_directories",
            "plan",
            "--deployment",
            str(deployment),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert completed.stderr == ""
    assert yaml.safe_load(completed.stdout)["error"] == {"code": "usage_error"}
