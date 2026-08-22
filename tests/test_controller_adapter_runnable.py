from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _request() -> dict[str, object]:
    return {
        "schema_version": "infralink.controller-adapter-request/v1",
        "registry_root": "/var/lib/infralink/registry",
        "registry_revision": "a" * 40,
        "host_id": "32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        "runtime_root": "/var/lib/infralink",
        "services_root": "/opt/services",
        "phase": "apply",
    }


def test_module_invokes_explicit_adapter_with_typed_stdin_request(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import json
import sys

request = json.load(sys.stdin)
assert request["registry_revision"] == "a" * 40
json.dump(
    {
        "schema_version": "infralink.controller-adapter-result/v1",
        "phase": "apply",
        "status": "applied",
        "registry_revision": request["registry_revision"],
        "actions": [{"category": "service", "state": "changed", "count": 1}],
        "evidence": [{"kind": "service", "status": "passed"}],
    },
    sys.stdout,
)
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_adapter_runnable",
            "invoke",
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(adapter),
        ],
        input=json.dumps(_request()),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schema_version": "infralink.ops.controller-adapter-transport/v1",
        "ok": True,
        "result": {
            "schema_version": "infralink.controller-adapter-result/v1",
            "phase": "apply",
            "status": "applied",
            "registry_revision": "a" * 40,
            "actions": [{"category": "service", "state": "changed", "count": 1}],
            "evidence": [{"kind": "service", "status": "passed"}],
        },
    }


def test_module_rejects_invalid_adapter_result_without_exposing_stderr(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import sys
print("private-adapter-detail", file=sys.stderr)
print("not json")
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_adapter_runnable",
            "invoke",
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(adapter),
        ],
        input=json.dumps(_request()),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 78
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schema_version": "infralink.ops.controller-adapter-transport/v1",
        "ok": False,
        "error": {"code": "adapter_transport_failed"},
    }


def test_module_rejects_invalid_request_before_starting_adapter(tmp_path: Path) -> None:
    marker = tmp_path / "adapter-ran"
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink_ops.controller_adapter_runnable",
            "invoke",
            "--adapter",
            sys.executable,
            "--adapter-arg",
            str(adapter),
        ],
        input="{}",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert not marker.exists()
    assert json.loads(completed.stdout) == {
        "schema_version": "infralink.ops.controller-adapter-transport/v1",
        "ok": False,
        "error": {"code": "request_invalid"},
    }


def test_module_emits_only_json_for_invalid_usage() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "infralink_ops.controller_adapter_runnable", "invoke"],
        input=json.dumps(_request()),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 64
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schema_version": "infralink.ops.controller-adapter-transport/v1",
        "ok": False,
        "error": {"code": "usage_error"},
    }
