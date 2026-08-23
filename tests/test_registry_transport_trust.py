from __future__ import annotations

import stat
from pathlib import Path

import pytest

from infralink_ops.registry_transport_trust import (
    RegistryTransportTrustError,
    materialize_registry_transport_trust,
)


def test_materializes_explicit_trust_atomically_with_private_mode(tmp_path: Path) -> None:
    destination = tmp_path / "registry-known_hosts"
    destination.write_text("old\n", encoding="utf-8")

    materialize_registry_transport_trust(
        content="[git.example]:2222 ssh-ed25519 AAAA\n", destination=destination
    )

    assert destination.read_text(encoding="utf-8") == "[git.example]:2222 ssh-ed25519 AAAA\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".registry-known_hosts.*"))


@pytest.mark.parametrize("content", ("", " \n", "host ssh-ed25519 AAAA\x00"))
def test_rejects_empty_or_nul_trust_without_changing_destination(
    tmp_path: Path, content: str
) -> None:
    destination = tmp_path / "registry-known_hosts"
    destination.write_text("existing\n", encoding="utf-8")

    with pytest.raises(RegistryTransportTrustError, match="registry_transport_trust_invalid"):
        materialize_registry_transport_trust(content=content, destination=destination)

    assert destination.read_text(encoding="utf-8") == "existing\n"


def test_rejects_missing_destination_parent(tmp_path: Path) -> None:
    with pytest.raises(
        RegistryTransportTrustError, match="registry_transport_trust_destination_invalid"
    ):
        materialize_registry_transport_trust(
            content="host ssh-ed25519 AAAA", destination=tmp_path / "missing" / "known_hosts"
        )
