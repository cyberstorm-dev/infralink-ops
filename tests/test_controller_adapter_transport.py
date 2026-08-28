from __future__ import annotations

import sys

import pytest
from infralink.controller_contracts import ControllerAdapterRequest

from infralink_ops.controller_adapter import (
    ControllerAdapterTransportError,
    invoke_controller_adapter,
)


def _request() -> ControllerAdapterRequest:
    return ControllerAdapterRequest(
        registry_root="/var/lib/infralink/registry",
        registry_revision="a" * 40,
        host_id="32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        runtime_root="/opt/infra",
        services_root="/opt/services",
        phase="apply",
    )


def test_invokes_fixed_adapter_argv_with_typed_json_request(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import json
import sys

request = json.load(sys.stdin)
assert request["schema_version"] == "infralink.controller-adapter-request/v1"
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

    result = invoke_controller_adapter([sys.executable, str(adapter)], _request())

    assert result.status == "applied"
    assert result.actions[0].category == "service"


def test_rejects_adapter_output_outside_the_public_result_contract(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import json
print(json.dumps({
    "schema_version": "infralink.controller-adapter-result/v1",
    "phase": "apply",
    "status": "applied",
    "registry_revision": "b" * 40,
    "actions": [{"category": "render", "state": "changed", "count": 1, "secret": "no"}],
    "evidence": [],
}))
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError, match="invalid result"):
        invoke_controller_adapter([sys.executable, str(adapter)], _request())


def test_rejects_adapter_result_for_a_different_registry_revision(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import json
print(json.dumps({
    "schema_version": "infralink.controller-adapter-result/v1",
    "phase": "apply",
    "status": "applied",
    "registry_revision": "b" * 40,
    "actions": [],
    "evidence": [],
}))
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError, match="registry revision mismatch"):
        invoke_controller_adapter([sys.executable, str(adapter)], _request())


def test_rejects_adapter_result_for_a_different_execution_phase(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import json
print(json.dumps({
    "schema_version": "infralink.controller-adapter-result/v1",
    "phase": "plan",
    "status": "planned",
    "registry_revision": "a" * 40,
    "actions": [],
    "evidence": [],
}))
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError, match="phase mismatch"):
        invoke_controller_adapter([sys.executable, str(adapter)], _request())


def test_does_not_expose_adapter_stderr_on_failure(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import sys
print("private-token-value", file=sys.stderr)
raise SystemExit(7)
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError) as raised:
        invoke_controller_adapter([sys.executable, str(adapter)], _request())

    assert "private-token-value" not in str(raised.value)
    assert raised.value.returncode == 7


def test_writes_opt_in_adapter_diagnostic_file_without_exposing_it(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    diagnostic_file = tmp_path / "adapter.stderr"
    adapter.write_text(
        """\\
import sys
print("private-token-value", file=sys.stderr)
raise SystemExit(7)
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError) as raised:
        invoke_controller_adapter(
            [sys.executable, str(adapter)], _request(), diagnostic_file=diagnostic_file
        )

    assert diagnostic_file.read_text(encoding="utf-8") == "private-token-value\n"
    assert diagnostic_file.stat().st_mode & 0o777 == 0o600
    assert "private-token-value" not in str(raised.value)


def test_exposes_only_an_allowlisted_adapter_diagnostic_code(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\\
import sys
print("private-token-value", file=sys.stderr)
print("infralink-adapter-diagnostic: declared_render_failed", file=sys.stderr)
raise SystemExit(7)
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError) as raised:
        invoke_controller_adapter([sys.executable, str(adapter)], _request())

    assert raised.value.category == "adapter_exit_nonzero"
    assert raised.value.diagnostic_code == "declared_render_failed"


def test_ignores_unstructured_or_malformed_adapter_diagnostics(tmp_path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """\
import sys
print("private-token-value", file=sys.stderr)
print("infralink-adapter-diagnostic: Authorization: Bearer private-token-value", file=sys.stderr)
raise SystemExit(7)
""",
        encoding="utf-8",
    )

    with pytest.raises(ControllerAdapterTransportError) as raised:
        invoke_controller_adapter([sys.executable, str(adapter)], _request())

    assert raised.value.diagnostic_code is None


def test_rejects_empty_adapter_argv() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        invoke_controller_adapter([], _request())
