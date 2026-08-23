from __future__ import annotations


def test_host_interface_assets_are_packaged_with_canonical_runtime_contract() -> None:
    from infralink_ops.host_interface_assets import asset_path

    operator_cli = asset_path("infralink")
    launcher = asset_path("infralink-host")
    service = asset_path("infralink-host-reconcile.service")
    timer = asset_path("infralink-host-reconcile.timer")

    assert all(path.is_file() for path in (operator_cli, launcher, service, timer))
    operator_cli_source = operator_cli.read_text(encoding="utf-8")
    assert "--network=host" in operator_cli_source
    assert "INFRALINK_CONTROL_ROOT=/app" in operator_cli_source
    assert (
        "INFRALINK_EDGES=/var/lib/infralink/registry/network/main-dev/edges/edges.yml"
        in operator_cli_source
    )
    assert (
        "INFRALINK_OBSERVATION_PLAN="
        "/var/lib/infralink/registry/operations/observation/core-plan.json" in operator_cli_source
    )
    assert (
        "INFRALINK_ADAPTER_BINDINGS="
        "/var/lib/infralink/registry/operations/observation/adapter-bindings.yml"
        in operator_cli_source
    )
    assert (
        "INFRALINK_GATUS_FRAGMENT="
        "/var/lib/infralink/registry/operations/observation/rendered/gatus/core-dependencies.yml"
        in operator_cli_source
    )
    assert "-e INFRALINK_GATUS_URL" in operator_cli_source
    assert "-e INFRALINK_GATUS_TOKEN" in operator_cli_source
    assert "/var/lib/infralink/registry" in launcher.read_text(encoding="utf-8")
    assert "--pull always" in launcher.read_text(encoding="utf-8")
    launcher_source = launcher.read_text(encoding="utf-8")
    doctor_runner = launcher_source.split("run_reconcile()", maxsplit=1)[0]
    assert "--privileged" not in doctor_runner
    assert "--pid=host" not in doctor_runner
    assert "--privileged" in launcher_source.split("run_reconcile()", maxsplit=1)[1]
    assert "--pid=host" in launcher_source.split("run_reconcile()", maxsplit=1)[1]
    assert "doctor)\n        run_normal" in launcher_source
    assert "reconcile)\n        run_reconcile" in launcher_source
    reconcile_runner = launcher_source.split("run_reconcile()", maxsplit=1)[1]
    assert "-e INFRALINK_HOST_ROOT=/infralink-host-interface" in reconcile_runner
    assert "src=/usr/local/bin,dst=/infralink-host-interface/usr/local/bin" in launcher_source
    assert "src=/usr/local/sbin,dst=/infralink-host-interface/usr/local/sbin" in launcher_source
    assert "src=/etc/systemd/system,dst=/infralink-host-interface/etc/systemd/system" in launcher_source
    assert "src=/usr/local/bin/infralink,dst=/usr/local/bin/infralink" not in launcher_source
    assert "src=/usr/local/bin,dst=/usr/local/bin" not in launcher_source
    assert "src=/usr/local/sbin,dst=/usr/local/sbin" not in doctor_runner
    assert "src=/etc/systemd/system,dst=/etc/systemd/system" not in doctor_runner
    assert service.read_text(encoding="utf-8") == (
        "[Unit]\n"
        "Description=Infralink controller reconcile\n"
        "Wants=network-online.target\n"
        "After=network-online.target docker.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/sbin/infralink-host reconcile\n"
    )
    assert "OnUnitActiveSec=5min" in timer.read_text(encoding="utf-8")
