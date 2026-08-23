from __future__ import annotations


def test_host_interface_assets_are_packaged_with_canonical_runtime_contract() -> None:
    from infralink_ops.host_interface_assets import asset_path

    launcher = asset_path("infralink-host")
    service = asset_path("infralink-host-reconcile.service")
    timer = asset_path("infralink-host-reconcile.timer")

    assert all(path.is_file() for path in (launcher, service, timer))
    assert "/var/lib/infralink/registry" in launcher.read_text(encoding="utf-8")
    assert "--pull always" in launcher.read_text(encoding="utf-8")
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
