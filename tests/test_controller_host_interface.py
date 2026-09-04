from __future__ import annotations

import stat
import subprocess
from pathlib import Path

SYSTEMD_RELOAD = [
    "nsenter",
    "--target",
    "1",
    "--mount",
    "--pid",
    "--",
    "systemctl",
    "daemon-reload",
]


def inactive_legacy_systemd(
    command: list[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    if "is-active" in command:
        return subprocess.CompletedProcess(command, 3, "inactive\n", "")
    if "is-enabled" in command:
        return subprocess.CompletedProcess(command, 1, "disabled\n", "")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_refresh_materializes_only_packaged_host_interface_assets_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    calls: list[list[str]] = []

    def reload_systemd(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return inactive_legacy_systemd(command)

    monkeypatch.setattr(host_interface.subprocess, "run", reload_systemd)
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    runtime = host_root / "usr/libexec/infralink/runtime"
    service = host_root / "etc/systemd/system/infralink-host-reconcile.service"
    timer = host_root / "etc/systemd/system/infralink-host-reconcile.timer"
    assert status == 0
    assert payload["schema_version"] == "infralink.ops.host-interface/v1"
    assert payload["ok"] is True
    assert payload["result"] == {
        "changed": True,
        "systemd_reloaded": True,
        "retired_assets": [],
        "assets": [
            {"path": "/usr/local/bin/infralink", "mode": "0755"},
            {"path": "/usr/libexec/infralink/runtime", "mode": "0755"},
            {"path": "/etc/systemd/system/infralink-host-reconcile.service", "mode": "0644"},
            {"path": "/etc/systemd/system/infralink-host-reconcile.timer", "mode": "0644"},
        ],
    }
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o755
    assert stat.S_IMODE(service.stat().st_mode) == 0o644
    assert stat.S_IMODE(timer.stat().st_mode) == 0o644
    assert calls == [
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.service",
        ],
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ],
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-enabled",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ],
        SYSTEMD_RELOAD,
    ]


def test_refresh_is_idempotent_without_reloading_systemd_when_assets_match(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_root = tmp_path / "host"
    host_root.mkdir()
    first, first_status = host_interface.main(["refresh", "--host-root", str(host_root)])
    assert first_status == 0
    assert first["result"]["changed"] is True

    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command),
            inactive_legacy_systemd(command),
        )[1],
    )
    second, second_status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert second_status == 0
    assert second["result"]["changed"] is False
    assert second["result"]["systemd_reloaded"] is False
    assert calls == [
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.service",
        ],
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ],
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-enabled",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ],
    ]


def test_refresh_retires_the_legacy_public_looking_launcher_after_unit_reload(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_root = tmp_path / "host"
    legacy = host_root / "usr/local/sbin/infralink-host"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("#!/bin/sh\n", encoding="utf-8")

    host_interface.main(["refresh", "--host-root", str(host_root)])
    legacy = host_root / "usr/local/sbin/infralink-host"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("#!/bin/sh\n", encoding="utf-8")

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["changed"] is True
    assert payload["result"]["retired_assets"] == ["/usr/local/sbin/infralink-host"]
    assert not legacy.exists()
    assert (host_root / "usr/libexec/infralink/runtime").is_file()


def test_refresh_retires_inactive_legacy_v2_reconcile_assets(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_interface.main(["refresh", "--host-root", str(host_root)])

    legacy_paths = (
        "usr/local/sbin/self-deploy-v2-reconcile",
        "etc/systemd/system/self-deploy-v2-reconcile.service",
        "etc/systemd/system/self-deploy-v2-reconcile.timer",
        "etc/systemd/system/self-deploy-v2-reconcile.service.d/environment.conf",
    )
    for relative in legacy_paths:
        destination = host_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("legacy\n", encoding="utf-8")

    calls: list[list[str]] = []

    def systemd(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "is-active" in command:
            return subprocess.CompletedProcess(command, 3, "inactive\n", "")
        if "is-enabled" in command:
            return subprocess.CompletedProcess(command, 1, "disabled\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(host_interface.subprocess, "run", systemd)
    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["retired_assets"] == [
        "/etc/systemd/system/self-deploy-v2-reconcile.service",
        "/etc/systemd/system/self-deploy-v2-reconcile.service.d/environment.conf",
        "/etc/systemd/system/self-deploy-v2-reconcile.timer",
        "/usr/local/sbin/self-deploy-v2-reconcile",
    ]
    assert all(not (host_root / relative).exists() for relative in legacy_paths)
    assert [command for command in calls if "is-active" in command] == [
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.service",
        ],
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-active",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ],
    ]
    assert [command for command in calls if "is-enabled" in command] == [
        [
            *SYSTEMD_RELOAD[:-2],
            "systemctl",
            "is-enabled",
            "--quiet",
            "self-deploy-v2-reconcile.timer",
        ]
    ]
    assert calls[-1] == SYSTEMD_RELOAD


def test_refresh_refuses_to_retire_an_active_legacy_v2_reconcile_timer(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()
    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_interface.main(["refresh", "--host-root", str(host_root)])

    timer = host_root / "etc/systemd/system/self-deploy-v2-reconcile.timer"
    timer.parent.mkdir(parents=True, exist_ok=True)
    timer.write_text("legacy\n", encoding="utf-8")

    def systemd(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "self-deploy-v2-reconcile.timer" and "is-active" in command:
            return subprocess.CompletedProcess(command, 0, "active\n", "")
        return inactive_legacy_systemd(command)

    monkeypatch.setattr(host_interface.subprocess, "run", systemd)
    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "legacy_reconcile_active"}
    assert timer.is_file()


def test_refresh_refuses_a_loaded_legacy_v2_timer_without_a_legacy_file(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()

    def systemd(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "self-deploy-v2-reconcile.timer" and "is-active" in command:
            return subprocess.CompletedProcess(command, 0, "active\n", "")
        return inactive_legacy_systemd(command)

    monkeypatch.setattr(host_interface.subprocess, "run", systemd)
    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "legacy_reconcile_active"}
    assert not (host_root / "usr/local/bin/infralink").exists()


def test_refresh_preflights_every_destination_before_writing(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_root = tmp_path / "host"
    host_root.mkdir()
    unsafe_parent = host_root / "etc/systemd"
    unsafe_parent.mkdir(parents=True)
    unsafe_parent.rmdir()
    unsafe_parent.symlink_to(tmp_path / "outside", target_is_directory=True)

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "host_interface_path_unsafe"}
    assert not (host_root / "usr/libexec/infralink/runtime").exists()


def test_refresh_rejects_a_symlinked_retired_launcher_parent(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", inactive_legacy_systemd)
    host_root = tmp_path / "host"
    (host_root / "usr").mkdir(parents=True)
    (host_root / "usr/local").symlink_to(tmp_path / "outside", target_is_directory=True)

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "host_interface_path_unsafe"}
    assert not (host_root / "usr/libexec/infralink/runtime").exists()


def test_refresh_rolls_back_changed_units_when_systemd_reload_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()

    def reload_failure(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "is-active" in command or "is-enabled" in command:
            return inactive_legacy_systemd(command)
        raise subprocess.CalledProcessError(1, "systemctl")

    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        reload_failure,
    )
    first, first_status = host_interface.main(["refresh", "--host-root", str(host_root)])
    assert first_status == 78
    assert first["error"] == {"code": "host_interface_systemd_reload_failed"}
    assert not (host_root / "etc/systemd/system/infralink-host-reconcile.service").exists()
    assert not (host_root / "etc/systemd/system/infralink-host-reconcile.timer").exists()

    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command),
            inactive_legacy_systemd(command),
        )[1],
    )
    second, second_status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert second_status == 0
    assert second["result"]["changed"] is True
    assert second["result"]["systemd_reloaded"] is True
    assert calls[-1] == SYSTEMD_RELOAD


def test_refresh_restores_legacy_assets_when_systemd_reload_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()
    launcher = host_root / "usr/local/sbin/self-deploy-v2-reconcile"
    unit = host_root / "etc/systemd/system/self-deploy-v2-reconcile.timer"
    launcher.parent.mkdir(parents=True)
    unit.parent.mkdir(parents=True)
    launcher.write_text("legacy launcher\n", encoding="utf-8")
    unit.write_text("legacy unit\n", encoding="utf-8")

    def reload_failure(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "is-active" in command or "is-enabled" in command:
            return inactive_legacy_systemd(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(host_interface.subprocess, "run", reload_failure)
    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "host_interface_systemd_reload_failed"}
    assert launcher.read_text(encoding="utf-8") == "legacy launcher\n"
    assert unit.read_text(encoding="utf-8") == "legacy unit\n"
