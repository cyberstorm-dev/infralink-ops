from __future__ import annotations

from pathlib import Path


def test_transition_refreshes_private_runtime_then_persists_verified_seed(
    tmp_path: Path, monkeypatch
) -> None:
    from infralink_ops import controller_host_transition as transition

    host_root = tmp_path / "host"
    host_root.mkdir()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        transition.host_interface,
        "refresh",
        lambda root: calls.append(("refresh", root)) or {"changed": True},
    )
    monkeypatch.setattr(
        transition.host_interface,
        "transition_controller_seed",
        lambda root, reference: calls.append(("seed", (root, reference))) or {"changed": True},
    )
    reference = "ghcr.io/cyberstorm-dev/infralink-ops-controller@sha256:" + "a" * 64

    payload, status = transition.main(
        ["transition", "--host-root", str(host_root), "--controller-reference", reference]
    )

    assert status == 0
    assert payload["ok"] is True
    assert calls == [("refresh", host_root), ("seed", (host_root, reference))]


def test_transition_refuses_seed_mutation_when_runtime_refresh_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from infralink_ops import controller_host_transition as transition

    host_root = tmp_path / "host"
    host_root.mkdir()
    called: list[bool] = []
    monkeypatch.setattr(
        transition.host_interface,
        "refresh",
        lambda _root: (_ for _ in ()).throw(
            transition.host_interface.HostInterfaceError("host_interface_refresh_failed")
        ),
    )
    monkeypatch.setattr(
        transition.host_interface,
        "transition_controller_seed",
        lambda *_args: called.append(True),
    )
    reference = "ghcr.io/cyberstorm-dev/infralink-ops-controller@sha256:" + "a" * 64

    payload, status = transition.main(
        ["transition", "--host-root", str(host_root), "--controller-reference", reference]
    )

    assert status == 78
    assert payload["error"] == {"code": "controller_seed_transition_failed"}
    assert called == []
