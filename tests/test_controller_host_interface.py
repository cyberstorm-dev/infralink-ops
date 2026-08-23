from __future__ import annotations

import stat
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


def test_refresh_materializes_only_packaged_host_interface_assets_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    calls: list[list[str]] = []

    def reload_systemd(command: list[str], **_: object) -> None:
        calls.append(command)

    monkeypatch.setattr(host_interface.subprocess, "run", reload_systemd)
    host_root = tmp_path / "host"
    host_root.mkdir()

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    launcher = host_root / "usr/local/sbin/infralink-host"
    service = host_root / "etc/systemd/system/infralink-host-reconcile.service"
    timer = host_root / "etc/systemd/system/infralink-host-reconcile.timer"
    assert status == 0
    assert payload["schema_version"] == "infralink.ops.host-interface/v1"
    assert payload["ok"] is True
    assert payload["result"] == {
        "changed": True,
        "systemd_reloaded": True,
        "assets": [
            {"path": "/usr/local/sbin/infralink-host", "mode": "0755"},
            {"path": "/etc/systemd/system/infralink-host-reconcile.service", "mode": "0644"},
            {"path": "/etc/systemd/system/infralink-host-reconcile.timer", "mode": "0644"},
        ],
    }
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    assert stat.S_IMODE(service.stat().st_mode) == 0o644
    assert stat.S_IMODE(timer.stat().st_mode) == 0o644
    assert calls == [SYSTEMD_RELOAD]


def test_refresh_is_idempotent_and_reloads_systemd_when_assets_match(
    tmp_path: Path, monkeypatch
) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", lambda *_args, **_kwargs: None)
    host_root = tmp_path / "host"
    host_root.mkdir()
    first, first_status = host_interface.main(["refresh", "--host-root", str(host_root)])
    assert first_status == 0
    assert first["result"]["changed"] is True

    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    second, second_status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert second_status == 0
    assert second["result"]["changed"] is False
    assert second["result"]["systemd_reloaded"] is True
    assert calls == [SYSTEMD_RELOAD]


def test_refresh_preflights_every_destination_before_writing(tmp_path: Path, monkeypatch) -> None:
    import infralink_ops.controller_host_interface as host_interface

    monkeypatch.setattr(host_interface.subprocess, "run", lambda *_args, **_kwargs: None)
    host_root = tmp_path / "host"
    host_root.mkdir()
    unsafe_parent = host_root / "etc/systemd"
    unsafe_parent.mkdir(parents=True)
    unsafe_parent.rmdir()
    unsafe_parent.symlink_to(tmp_path / "outside", target_is_directory=True)

    payload, status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert status == 78
    assert payload["error"] == {"code": "host_interface_path_unsafe"}
    assert not (host_root / "usr/local/sbin/infralink-host").exists()


def test_refresh_retries_systemd_reload_after_a_prior_failure(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    import infralink_ops.controller_host_interface as host_interface

    host_root = tmp_path / "host"
    host_root.mkdir()

    def reload_failure(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "systemctl")

    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        reload_failure,
    )
    first, first_status = host_interface.main(["refresh", "--host-root", str(host_root)])
    assert first_status == 78
    assert first["error"] == {"code": "host_interface_systemd_reload_failed"}

    calls: list[list[str]] = []
    monkeypatch.setattr(
        host_interface.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    second, second_status = host_interface.main(["refresh", "--host-root", str(host_root)])

    assert second_status == 0
    assert second["result"]["changed"] is False
    assert calls == [SYSTEMD_RELOAD]
