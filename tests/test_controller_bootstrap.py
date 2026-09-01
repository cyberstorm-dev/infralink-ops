from __future__ import annotations

import stat
import subprocess
from pathlib import Path


def _environment() -> dict[str, str]:
    return {
        "INFRALINK_HOST_UUID": "9157ddeb-cb6d-4d55-8252-9db358f5d932",
        "INFRALINK_CONTROLLER_IMAGE": "ghcr.io/example/infralink-controller:main",
        "BWS_ACCESS_TOKEN": "test-token",
        "INFRALINK_REGISTRY_DEPLOY_KEY_SECRET_ID": "registry-read-key",
        "INFRALINK_REGISTRY_REPO_URL": "ssh://git@example.invalid/infra-registry.git",
        "INFRALINK_REGISTRY_REF": "main",
        "INFRALINK_REGISTRY_KNOWN_HOSTS": "example.invalid ssh-ed25519 AAAA",
    }


def test_plan_reports_exact_bootstrap_writes_without_mutating_host_root(tmp_path: Path) -> None:
    from infralink_ops.controller_bootstrap import main

    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = main(
        ["plan", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 0
    assert payload == {
        "schema_version": "infralink.ops.controller-bootstrap/v1",
        "ok": True,
        "command": {"path": ["plan"], "args": {"host_root": str(host_root)}},
        "result": {
            "host_uuid": "9157ddeb-cb6d-4d55-8252-9db358f5d932",
            "registry": {
                "remote": "ssh://git@example.invalid/infra-registry.git",
                "ref": "main",
                "root": "/var/lib/infralink/registry",
            },
            "writes": [
                "/etc/machine-uuid",
                "/etc/infralink/host.env",
                "/etc/infralink/registry-read",
                "/etc/infralink/registry-known_hosts",
                "/var/lib/infralink/registry",
                "/usr/local/bin/infralink",
                "/usr/local/sbin/infralink-host",
                "/etc/systemd/system/infralink-host-reconcile.service",
                "/etc/systemd/system/infralink-host-reconcile.timer",
            ],
        },
        "next_actions": [],
        "meta": {"truncated": False},
    }
    assert list(host_root.iterdir()) == []


def test_plan_fails_closed_when_required_registry_trust_is_absent(tmp_path: Path) -> None:
    from infralink_ops.controller_bootstrap import main

    host_root = tmp_path / "host"
    host_root.mkdir()
    environment = _environment()
    del environment["INFRALINK_REGISTRY_KNOWN_HOSTS"]

    payload, status = main(
        ["plan", "--host-root", str(host_root)],
        environ=environment,
    )

    assert status == 64
    assert payload["ok"] is False
    assert payload["error"] == {"code": "registry_transport_trust_required"}
    assert list(host_root.iterdir()) == []


def test_explicit_empty_environment_does_not_fall_back_to_process_environment(
    tmp_path: Path, monkeypatch
) -> None:
    from infralink_ops.controller_bootstrap import main

    host_root = tmp_path / "host"
    host_root.mkdir()
    for key, value in _environment().items():
        monkeypatch.setenv(key, value)

    payload, status = main(["plan", "--host-root", str(host_root)], environ={})

    assert status == 64
    assert payload["error"] == {"code": "bootstrap_configuration_required"}


def test_apply_materializes_only_bootstrap_owned_files_and_enables_the_single_timer(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    host_root = tmp_path / "host"
    host_root.mkdir()
    refreshed: list[Path] = []
    enabled: list[Path] = []
    monkeypatch.setattr(bootstrap, "_read_bws_secret", lambda *_args: "private-key")
    monkeypatch.setattr(
        bootstrap,
        "_initialize_registry",
        lambda *_args, **_kwargs: "0123456789abcdef0123456789abcdef01234567",
    )
    monkeypatch.setattr(
        bootstrap.host_interface,
        "refresh",
        lambda root: refreshed.append(root) or {"changed": True, "systemd_reloaded": True},
    )
    monkeypatch.setattr(bootstrap, "_enable_reconcile_timer", lambda root: enabled.append(root))
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_active", lambda _unit: False)
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_enabled", lambda _unit: False)

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["registry"]["revision"] == "0123456789abcdef0123456789abcdef01234567"
    assert (host_root / "etc/machine-uuid").read_text(encoding="utf-8") == (
        "9157ddeb-cb6d-4d55-8252-9db358f5d932\n"
    )
    assert stat.S_IMODE((host_root / "etc/machine-uuid").stat().st_mode) == 0o644
    assert (host_root / "etc/infralink/registry-read").read_text(encoding="utf-8") == (
        "private-key\n"
    )
    assert stat.S_IMODE((host_root / "etc/infralink/registry-read").stat().st_mode) == 0o600
    host_environment = (host_root / "etc/infralink/host.env").read_text(encoding="utf-8")
    assert "INFRALINK_HOST_UUID=9157ddeb-cb6d-4d55-8252-9db358f5d932" in host_environment
    assert "BWS_ACCESS_TOKEN=test-token" in host_environment
    assert refreshed == [host_root]
    assert enabled == [host_root]


def test_apply_fails_before_writing_when_a_legacy_apply_loop_is_present(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    host_root = tmp_path / "host"
    (host_root / "etc/cron.d").mkdir(parents=True)
    (host_root / "etc/cron.d/self-deploy").write_text("* * * * * root false\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_read_bws_secret", lambda *_args: "private-key")

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 78
    assert payload["error"] == {"code": "competing_apply_loop_detected"}
    assert not (host_root / "etc/machine-uuid").exists()
    assert not (host_root / "etc/infralink").exists()


def test_apply_fails_before_writing_when_another_reconcile_timer_is_active(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_active", lambda _unit: True)

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 78
    assert payload["error"] == {"code": "competing_apply_loop_detected"}
    assert not (host_root / "etc/machine-uuid").exists()


def test_apply_fails_before_writing_when_another_reconcile_timer_is_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_active", lambda _unit: False)
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_enabled", lambda _unit: True)

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 78
    assert payload["error"] == {"code": "competing_apply_loop_detected"}
    assert not (host_root / "etc/machine-uuid").exists()


def test_apply_fails_before_writing_when_legacy_reconcile_service_is_active(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(
        bootstrap,
        "_systemd_unit_is_active",
        lambda unit: unit == "self-deploy-v2-reconcile.service",
    )
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_enabled", lambda _unit: False)
    monkeypatch.setattr(bootstrap, "_read_bws_secret", lambda *_args: "private-key")

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 78
    assert payload["error"] == {"code": "competing_apply_loop_detected"}
    assert not (host_root / "etc/machine-uuid").exists()


def test_apply_translates_registry_trust_write_failure_to_yaml_error(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap
    from infralink_ops.registry_transport_trust import RegistryTransportTrustError

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_active", lambda _unit: False)
    monkeypatch.setattr(bootstrap, "_systemd_unit_is_enabled", lambda _unit: False)
    monkeypatch.setattr(bootstrap, "_read_bws_secret", lambda *_args: "private-key")
    monkeypatch.setattr(
        bootstrap,
        "materialize_registry_transport_trust",
        lambda **_kwargs: (_ for _ in ()).throw(RegistryTransportTrustError("failed")),
    )

    payload, status = bootstrap.main(
        ["apply", "--host-root", str(host_root)],
        environ=_environment(),
    )

    assert status == 78
    assert payload["error"] == {"code": "registry_transport_trust_write_failed"}


def test_missing_legacy_timer_is_not_treated_as_an_apply_loop_state_error(monkeypatch) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 4, "", ""),
    )

    assert bootstrap._systemd_unit_is_active("self-deploy-v2-reconcile.timer") is False


def test_apply_initializes_the_single_registry_checkout_when_its_parent_is_missing(
    tmp_path: Path,
) -> None:
    import infralink_ops.controller_bootstrap as bootstrap

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=origin, check=True)
    (origin / "registry.yml").write_text("schema_version: test/v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "registry.yml"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=origin, check=True)
    expected_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, check=True, capture_output=True, text=True
    ).stdout.strip()

    host_root = tmp_path / "host"
    (host_root / "etc/infralink").mkdir(parents=True)
    (host_root / "etc/infralink/registry-read").write_text("unused\n", encoding="utf-8")
    (host_root / "etc/infralink/registry-known_hosts").write_text("unused\n", encoding="utf-8")
    configuration = bootstrap.BootstrapConfiguration(
        host_uuid="9157ddeb-cb6d-4d55-8252-9db358f5d932",
        controller_image="ghcr.io/example/controller:main",
        bws_access_token="test-token",
        deploy_key_secret_id="registry-read-key",
        registry_remote=str(origin),
        registry_ref="main",
        registry_known_hosts="unused",
    )

    revision = bootstrap._initialize_registry(host_root, configuration)

    registry = host_root / "var/lib/infralink/registry"
    assert revision == expected_revision
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=registry, check=True, capture_output=True, text=True
        ).stdout.strip()
        == expected_revision
    )
