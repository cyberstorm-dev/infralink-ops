from __future__ import annotations


def test_host_interface_assets_are_packaged_with_canonical_runtime_contract() -> None:
    from infralink_ops.host_interface_assets import asset_path

    operator_cli = asset_path("infralink")
    runtime = asset_path("infralink-runtime")
    service = asset_path("infralink-host-reconcile.service")
    timer = asset_path("infralink-host-reconcile.timer")

    assert all(path.is_file() for path in (operator_cli, runtime, service, timer))
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
    assert "-e INFRALINK_CONTROLLER_IMAGE" in operator_cli_source
    runtime_source = runtime.read_text(encoding="utf-8")
    assert "/var/lib/infralink/registry" in runtime_source
    assert "--pull always" in runtime_source
    assert "--privileged" in runtime_source
    assert "--pid=host" in runtime_source
    assert "reconcile" in runtime_source
    assert "Usage:" not in runtime_source
    assert "-e INFRALINK_HOST_ROOT=/infralink-host-interface" in runtime_source
    assert "src=/usr/local/bin,dst=/infralink-host-interface/usr/local/bin" in runtime_source
    assert "src=/usr/local/sbin,dst=/infralink-host-interface/usr/local/sbin" in runtime_source
    assert (
        "src=/usr/libexec/infralink,dst=/infralink-host-interface/usr/libexec/infralink"
        in runtime_source
    )
    assert (
        "src=/etc/systemd/system,dst=/infralink-host-interface/etc/systemd/system" in runtime_source
    )
    assert "Usage: infralink-host" not in runtime_source
    assert service.read_text(encoding="utf-8") == (
        "[Unit]\n"
        "Description=Infralink controller reconcile\n"
        "Wants=network-online.target\n"
        "After=network-online.target docker.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/libexec/infralink/runtime\n"
    )
    assert "OnUnitActiveSec=5min" in timer.read_text(encoding="utf-8")
