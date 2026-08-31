"""Materialize explicit SSH registry transport trust without selecting registry state."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class RegistryTransportTrustError(ValueError):
    """Registry SSH trust input cannot be safely materialized."""


def materialize_registry_transport_trust(*, content: str, destination: Path) -> None:
    """Atomically replace one bootstrap-owned known-hosts file with mode 0600."""

    if not content.strip() or "\x00" in content:
        raise RegistryTransportTrustError("registry_transport_trust_invalid")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise RegistryTransportTrustError("registry_transport_trust_destination_invalid")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-known_hosts.", dir=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content if content.endswith("\n") else f"{content}\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except OSError as error:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise RegistryTransportTrustError("registry_transport_trust_write_failed") from error
