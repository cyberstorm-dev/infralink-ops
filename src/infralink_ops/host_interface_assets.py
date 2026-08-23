"""Packaged deployment-neutral assets for the host reconcile interface."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_ASSET_NAMES = frozenset(
    {
        "infralink",
        "infralink-host",
        "infralink-host-reconcile.service",
        "infralink-host-reconcile.timer",
    }
)


def asset_path(name: str) -> Path:
    """Return one packaged host-interface asset from the installed Ops distribution."""

    if name not in _ASSET_NAMES:
        raise ValueError("host_interface_asset_unknown")
    return Path(files("infralink_ops").joinpath("assets", name))
