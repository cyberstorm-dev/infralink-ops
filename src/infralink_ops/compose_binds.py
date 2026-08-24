"""Parse absolute host bind mounts from a Compose document."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class ComposeBindError(ValueError):
    """A Compose document does not declare bind mounts in the supported form."""


@dataclass(frozen=True, order=True)
class ComposeBindMount:
    """One absolute host-path bind mount declared by Compose."""

    source: str
    target: str


def parse_compose_bind_mounts(compose: Path) -> tuple[ComposeBindMount, ...]:
    """Return deduplicated absolute bind mounts from short and long Compose syntax."""

    try:
        document = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
            raise ValueError
        mounts: set[ComposeBindMount] = set()
        for raw_service in document["services"].values():
            if not isinstance(raw_service, dict):
                raise ValueError
            volumes = raw_service.get("volumes", [])
            if not isinstance(volumes, list):
                raise ValueError
            for volume in volumes:
                mount = _bind_mount(volume)
                if mount is not None:
                    mounts.add(mount)
        return tuple(sorted(mounts))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        raise ComposeBindError("compose_bind_invalid") from None


def _bind_mount(value: object) -> ComposeBindMount | None:
    source: str | None = None
    target: str | None = None
    if isinstance(value, str):
        source, separator, remainder = value.partition(":")
        target, _, _ = remainder.partition(":")
        if not separator:
            return None
        if not source.startswith("/"):
            return None
    elif isinstance(value, dict) and value.get("type") == "bind":
        source = value.get("source")
        target = value.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError
    elif isinstance(value, dict):
        return None
    else:
        raise ValueError
    if not source or not target or not source.startswith("/") or not target.startswith("/"):
        raise ValueError
    return ComposeBindMount(source=source, target=target)
