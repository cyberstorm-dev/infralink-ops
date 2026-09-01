"""Private controller runtime lifecycle tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from infralink_ops import controller_runtime

HOST_ID = "9157ddeb-cb6d-4d55-8252-9db358f5d932"
REVISION = "a" * 40
DIGEST = "ghcr.io/example/infralink-controller@sha256:" + "b" * 64


def _environment(*, handoff: bool = False) -> dict[str, str]:
    values = {
        "INFRALINK_HOST_UUID": HOST_ID,
        "INFRALINK_CONTROLLER_IMAGE": "ghcr.io/example/infralink-controller:main",
        "INFRALINK_REGISTRY_REPO_URL": "ssh://git@example.invalid/infra-registry.git",
        "INFRALINK_REGISTRY_REF": "main",
    }
    if handoff:
        values.update(
            {
                "INFRALINK_CONTROLLER_HANDOFF_DIGEST": DIGEST,
                "INFRALINK_CONTROLLER_HANDOFF_REGISTRY_REVISION": REVISION,
                "INFRALINK_CONTROLLER_HANDOFF_REFERENCE": (
                    "ghcr.io/example/infralink-controller:main"
                ),
            }
        )
    return values


def test_reconcile_requires_the_fixed_controller_configuration() -> None:
    payload, status = controller_runtime.main(["reconcile"], environ={})

    assert status == 64
    assert payload == {
        "schema_version": "infralink.ops.controller-runtime/v1",
        "ok": False,
        "error": {"code": "controller_configuration_required"},
    }


def test_private_runtime_rejects_unknown_modes() -> None:
    payload, status = controller_runtime.main(["unknown"], environ={})

    assert status == 64
    assert payload == {
        "schema_version": "infralink.ops.controller-runtime/v1",
        "ok": False,
        "error": {"code": "usage_error"},
    }


def test_inner_reconcile_uses_one_handoff_revision_and_publishes_success(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(controller_runtime, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(controller_runtime, "REGISTRY_ROOT", tmp_path / "registry")
    monkeypatch.setattr(controller_runtime, "SERVICES_ROOT", tmp_path / "services")

    applied: list[tuple[str, str]] = []
    published: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        controller_runtime,
        "_inner_reconcile",
        lambda context, revision, digest: (
            applied.append((revision, digest))
            or {
                "phase": "apply",
                "status": "applied",
                "registry_revision": revision,
                "actions": [],
                "evidence": [],
            }
        ),
    )
    monkeypatch.setattr(
        controller_runtime.controller_images, "prune_unused_images", lambda _: ({"status": "ok"}, 0)
    )
    monkeypatch.setattr(
        controller_runtime,
        "_publish_success",
        lambda _context, *, revision, controller_digest, adapter, cleanup: published.append(
            (revision, controller_digest, cleanup)
        ),
    )

    payload, status = controller_runtime.main(["reconcile"], environ=_environment(handoff=True))

    assert status == 0
    assert payload["result"]["registry_revision"] == REVISION
    assert applied == [(REVISION, DIGEST)]
    assert published == [(REVISION, DIGEST, {"status": "ok"})]


def test_invalid_handoff_fails_before_any_apply_stage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(controller_runtime, "RUNTIME_ROOT", tmp_path)
    applied: list[object] = []
    failures: list[str] = []
    monkeypatch.setattr(controller_runtime, "_inner_reconcile", lambda *_args: applied.append(True))
    monkeypatch.setattr(
        controller_runtime, "_publish_failure", lambda _context, error: failures.append(str(error))
    )
    environment = _environment(handoff=True)
    environment["INFRALINK_CONTROLLER_HANDOFF_REGISTRY_REVISION"] = "not-a-sha"

    payload, status = controller_runtime.main(["reconcile"], environ=environment)

    assert status == 78
    assert payload["error"] == {"code": "controller_handoff_invalid"}
    assert applied == []
    assert failures == ["controller_handoff_invalid"]


def test_reconcile_in_progress_preserves_last_completed_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    previous_evidence = runtime / "reconcile-result.yml"
    previous_evidence.write_text("status: success\n", encoding="utf-8")
    monkeypatch.setattr(controller_runtime, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(
        controller_runtime,
        "reconcile",
        lambda _context: (_ for _ in ()).throw(
            controller_runtime.ControllerRuntimeError(
                "controller_reconcile_in_progress", publish_evidence=False
            )
        ),
    )

    payload, status = controller_runtime.main(["reconcile"], environ=_environment(handoff=True))

    assert status == 78
    assert payload["error"] == {"code": "controller_reconcile_in_progress"}
    assert previous_evidence.read_text(encoding="utf-8") == "status: success\n"


def test_doctor_mode_preserves_the_existing_host_doctor_contract(monkeypatch) -> None:
    expected = {"schema_version": "infralink.controller-doctor/v1", "status": "healthy"}
    monkeypatch.setattr(controller_runtime.controller_doctor, "main", lambda argv: (expected, 0))

    payload, status = controller_runtime.main(["doctor"])

    assert status == 0
    assert payload is expected


def test_outer_reconcile_fetches_one_revision_then_handoffs_that_exact_image(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry"
    services = tmp_path / "services"
    runtime.mkdir()
    registry.mkdir()
    monkeypatch.setattr(controller_runtime, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(controller_runtime, "REGISTRY_ROOT", registry)
    monkeypatch.setattr(controller_runtime, "SERVICES_ROOT", services)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        controller_runtime,
        "fetch_configured_registry",
        lambda root, **kwargs: (
            calls.append(("fetch", (root, kwargs)))
            or type("Checkout", (), {"root": root, "revision": REVISION})()
        ),
    )
    monkeypatch.setattr(
        controller_runtime,
        "_runtime_directories",
        lambda context: calls.append(("runtime_directories", context.registry_root)),
    )
    monkeypatch.setattr(
        controller_runtime.controller_host_interface,
        "refresh",
        lambda root: calls.append(("host_interface", root)),
    )
    monkeypatch.setattr(
        controller_runtime,
        "resolve_controller_reference",
        lambda root, host_id, *, expected_revision: (
            calls.append(("controller_reference", (root, host_id, expected_revision)))
            or "ghcr.io/example/infralink-controller:main"
        ),
    )
    monkeypatch.setattr(
        controller_runtime,
        "_controller_digest",
        lambda reference, *, pull: calls.append(("controller_digest", (reference, pull))) or DIGEST,
    )
    monkeypatch.setattr(
        controller_runtime,
        "_handoff",
        lambda _context, *, revision, controller_digest, controller_reference: calls.append(
            ("handoff", (revision, controller_digest, controller_reference))
        ),
    )

    payload, status = controller_runtime.main(["reconcile"], environ=_environment())

    assert status == 0
    assert payload["result"] == {"status": "handoff_completed", "registry_revision": REVISION}
    assert calls[-1] == (
        "handoff",
        (REVISION, DIGEST, "ghcr.io/example/infralink-controller:main"),
    )
    assert [name for name, _ in calls] == [
        "fetch",
        "runtime_directories",
        "host_interface",
        "controller_reference",
        "controller_digest",
        "handoff",
    ]


def test_firewall_apply_creates_the_owned_table_before_replacing_it(
    tmp_path: Path, monkeypatch
) -> None:
    context = controller_runtime.ControllerContext(
        host_uuid=HOST_ID,
        controller_image="ghcr.io/example/infralink-controller:main",
        registry_remote="ssh://git@example.invalid/infra-registry.git",
        registry_ref="main",
        registry_root=tmp_path / "registry",
        runtime_root=tmp_path / "runtime",
        services_root=tmp_path / "services",
        registry_key=tmp_path / "registry-read",
        registry_known_hosts=tmp_path / "registry-known-hosts",
        host_root=tmp_path,
        textfile_directory=tmp_path,
        handoff_digest=DIGEST,
        environment=_environment(handoff=True),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        controller_runtime.controller_firewall,
        "main",
        lambda _argv: ({"ok": True, "result": {"status": "rendered", "rules": "rules\n"}}, 0),
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[1:3] == ["list", "table"] else 0)

    monkeypatch.setattr(controller_runtime.subprocess, "run", run)

    controller_runtime._apply_firewall(context, REVISION)

    assert calls == [
        ["nft", "list", "table", "inet", "infralink_filter"],
        ["nft", "add", "table", "inet", "infralink_filter"],
        ["nft", "-f", "-"],
    ]


def test_inner_reconcile_rejects_a_digest_not_bound_to_the_declared_controller(
    tmp_path: Path, monkeypatch
) -> None:
    context = controller_runtime.ControllerContext(
        host_uuid=HOST_ID,
        controller_image="ghcr.io/example/infralink-controller:main",
        registry_remote="ssh://git@example.invalid/infra-registry.git",
        registry_ref="main",
        registry_root=tmp_path / "registry",
        runtime_root=tmp_path / "runtime",
        services_root=tmp_path / "services",
        registry_key=tmp_path / "registry-read",
        registry_known_hosts=tmp_path / "registry-known-hosts",
        host_root=tmp_path,
        textfile_directory=tmp_path,
        handoff_digest=DIGEST,
        environment=_environment(handoff=True),
    )
    called: list[object] = []

    def verify(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(controller_runtime, "verify_registry_revision", verify)
    monkeypatch.setattr(
        controller_runtime,
        "resolve_controller_reference",
        lambda *_args, **_kwargs: "ghcr.io/example/infralink-controller:main",
    )
    monkeypatch.setattr(
        controller_runtime,
        "_controller_digest",
        lambda *_args, **_kwargs: "ghcr.io/example/infralink-controller@sha256:" + "c" * 64,
    )
    monkeypatch.setattr(
        controller_runtime,
        "resolve_host_images",
        lambda *_args, **_kwargs: called.append(True),
    )

    try:
        controller_runtime._inner_reconcile(context, REVISION, DIGEST)
    except controller_runtime.ControllerRuntimeError as error:
        assert str(error) == "controller_handoff_invalid"
    else:
        raise AssertionError("expected controller handoff validation to fail")
    assert called == []


def test_handoff_mounts_host_var_lib_for_runtime_and_textfile_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    context = controller_runtime.ControllerContext(
        host_uuid=HOST_ID,
        controller_image="ghcr.io/example/infralink-controller:main",
        registry_remote="ssh://git@example.invalid/infra-registry.git",
        registry_ref="main",
        registry_root=tmp_path / "var/lib/infralink/registry",
        runtime_root=tmp_path / "var/lib/infralink",
        services_root=tmp_path / "opt/services",
        registry_key=tmp_path / "etc/infralink/registry-read",
        registry_known_hosts=tmp_path / "etc/infralink/registry-known_hosts",
        host_root=tmp_path,
        textfile_directory=tmp_path / "var/lib/node_exporter/textfile_collector",
        handoff_digest=None,
        environment=_environment(),
    )
    command: list[str] = []
    monkeypatch.setattr(
        controller_runtime.subprocess,
        "run",
        lambda argv, **_kwargs: command.extend(argv) or subprocess.CompletedProcess(argv, 0),
    )

    controller_runtime._handoff(
        context,
        revision=REVISION,
        controller_digest=DIGEST,
        controller_reference="ghcr.io/example/infralink-controller:main",
    )

    assert "type=bind,src=/var/lib,dst=/var/lib" in command
    assert "type=bind,src=/var/lib/infralink,dst=/var/lib/infralink" not in command


def test_invalid_rendered_compose_stops_before_firewall_or_service_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    context = controller_runtime.ControllerContext(
        host_uuid=HOST_ID,
        controller_image="ghcr.io/example/infralink-controller:main",
        registry_remote="ssh://git@example.invalid/infra-registry.git",
        registry_ref="main",
        registry_root=tmp_path / "registry",
        runtime_root=tmp_path / "runtime",
        services_root=tmp_path / "services",
        registry_key=tmp_path / "registry-read",
        registry_known_hosts=tmp_path / "registry-known-hosts",
        host_root=tmp_path,
        textfile_directory=tmp_path,
        handoff_digest=DIGEST,
        environment=_environment(handoff=True),
    )
    mutations: list[str] = []

    def verify(*_args: object, **_kwargs: object) -> None:
        return None

    def invalid_compose(_context: object) -> None:
        raise controller_runtime.ControllerRuntimeError(
            "compose_validation_failed", stage="compose_validation"
        )

    monkeypatch.setattr(controller_runtime, "verify_registry_revision", verify)
    monkeypatch.setattr(
        controller_runtime,
        "resolve_controller_reference",
        lambda *_args, **_kwargs: "ghcr.io/example/infralink-controller:main",
    )
    monkeypatch.setattr(controller_runtime, "_controller_digest", lambda *_args, **_kwargs: DIGEST)
    monkeypatch.setattr(controller_runtime, "resolve_host_images", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(controller_runtime, "_secret_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        controller_runtime.template_renderer,
        "render_declared_host",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        controller_runtime,
        "_validate_compose",
        invalid_compose,
    )
    monkeypatch.setattr(
        controller_runtime,
        "_apply_firewall",
        lambda *_args: mutations.append("firewall"),
    )
    monkeypatch.setattr(
        controller_runtime,
        "_apply_compose",
        lambda *_args, **_kwargs: mutations.append("compose"),
    )

    try:
        controller_runtime._inner_reconcile(context, REVISION, DIGEST)
    except controller_runtime.ControllerRuntimeError as error:
        assert str(error) == "compose_validation_failed"
    else:
        raise AssertionError("expected compose validation failure")
    assert mutations == []


def test_all_representation_equivalent_services_still_remove_declared_orphans(
    tmp_path: Path, monkeypatch
) -> None:
    services = tmp_path / "services"
    services.mkdir()
    compose = services / "docker-compose.yml"
    compose.write_text(
        "services: {protected: {image: example@sha256:aaaaaaaa}}\n",
        encoding="utf-8",
    )
    context = controller_runtime.ControllerContext(
        host_uuid=HOST_ID,
        controller_image="ghcr.io/example/infralink-controller:main",
        registry_remote="ssh://git@example.invalid/infra-registry.git",
        registry_ref="main",
        registry_root=tmp_path / "registry",
        runtime_root=tmp_path / "runtime",
        services_root=services,
        registry_key=tmp_path / "registry-read",
        registry_known_hosts=tmp_path / "registry-known-hosts",
        host_root=tmp_path,
        textfile_directory=tmp_path,
        handoff_digest=DIGEST,
        environment=_environment(handoff=True),
    )
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        stdout = '[{"Service":"removed","ID":"0123456789ab"}]\n' if "ps" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout)

    monkeypatch.setattr(controller_runtime.subprocess, "run", run)

    assert controller_runtime._apply_compose(context, excluded={"protected"}) == 0
    assert calls == [
        ["docker", "compose", "-f", str(compose), "ps", "--all", "--format", "json"],
        ["docker", "rm", "-f", "0123456789ab"],
    ]
