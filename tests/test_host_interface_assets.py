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
    assert "INFRALINK_REGISTRY_REPO_URL" in operator_cli_source
    assert "INFRALINK_REGISTRY_REF" in operator_cli_source
    assert "INFRALINK_CONTROL_ROOT" not in operator_cli_source
    assert "BWS_ACCESS_TOKEN" not in operator_cli_source
    assert "--entrypoint /usr/local/bin/infralink" not in operator_cli_source
    assert '"$bootstrap_image" operator "$@"' in operator_cli_source
    assert "-e INFRALINK_GATUS_URL" in operator_cli_source
    assert "-e INFRALINK_GATUS_TOKEN" in operator_cli_source
    assert "INFRALINK_CONTROLLER_IMAGE" not in operator_cli_source
    assert "ghcr.io/cyberstorm-dev/infralink-ops-controller:main" in operator_cli_source
    runtime_source = runtime.read_text(encoding="utf-8")
    assert "/var/lib/infralink/registry" in runtime_source
    assert "--pull always" in runtime_source
    assert "INFRALINK_CONTROLLER_IMAGE" not in runtime_source
    assert "ghcr.io/cyberstorm-dev/infralink-ops-controller:main" in runtime_source
    assert "--privileged" in runtime_source
    assert "--pid=host" in runtime_source
    assert "src=/root/.docker/config.json,dst=/root/.docker/config.json,readonly" in runtime_source
    assert "reconcile" in runtime_source
    assert "Usage:" not in runtime_source
    assert "INFRALINK_HOST_ROOT" not in runtime_source
    assert "infralink-host-interface" not in runtime_source
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
