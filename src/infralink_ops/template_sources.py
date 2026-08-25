"""Revision-verified registry sources exposed to declared Jinja templates."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jinja2 import BaseLoader, TemplateNotFound

from .config_trees import verify_declared_registry_source_directory


class TemplateSourceError(ValueError):
    """A registry-declared template source is unavailable or unsafe."""


@dataclass(frozen=True)
class TemplateSource:
    """One named template directory within the selected registry revision."""

    id: str
    root: Path


def load_template_sources(
    *, registry: Path, expected_revision: str | None, host: Mapping[str, object]
) -> tuple[TemplateSource, ...]:
    """Validate the host's declared source aliases against the selected checkout.

    Sources are intentionally unavailable without the exact revision selected by
    the normal deployment path. This prevents a renderer from treating a local
    directory or submodule checkout as an independent desired-state authority.
    """

    declared = host.get("template_sources", [])
    if declared == []:
        return ()
    if not isinstance(declared, list):
        raise TemplateSourceError("template sources must be a list")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise TemplateSourceError("template sources require the selected registry revision")
    sources: list[TemplateSource] = []
    ids: set[str] = set()
    for entry in declared:
        if not isinstance(entry, Mapping):
            raise TemplateSourceError("template source must be a mapping")
        source_id, source_path = entry.get("id"), entry.get("source")
        if not _valid_id(source_id) or source_id in ids:
            raise TemplateSourceError("template source id is invalid")
        try:
            source_root = verify_declared_registry_source_directory(
                registry, expected_revision=expected_revision, source=source_path
            )
        except ValueError as error:
            raise TemplateSourceError("template source directory is invalid") from error
        ids.add(source_id)
        sources.append(TemplateSource(id=source_id, root=source_root))
    return tuple(sources)


class DeclaredTemplateSourceLoader(BaseLoader):
    """Resolve only the ``sources/<id>/...`` aliases declared for one host."""

    def __init__(self, sources: tuple[TemplateSource, ...]) -> None:
        self._sources = {source.id: source.root for source in sources}

    def get_source(self, environment: object, template: str):  # type: ignore[override]
        if not template.startswith("sources/"):
            raise TemplateNotFound(template)
        source_id, relative = _source_template_name(template)
        root = self._sources.get(source_id)
        if root is None:
            raise TemplateSourceError("template source is not declared")
        source = _safe_source_file(root, relative)
        try:
            body = source.read_text(encoding="utf-8")
        except OSError as error:
            raise TemplateSourceError("template source file is unavailable") from error
        mtime = source.stat().st_mtime
        return body, str(source), lambda: source.is_file() and source.stat().st_mtime == mtime


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value) and value.replace("-", "").replace("_", "").isalnum()
    )


def _source_template_name(template: str) -> tuple[str, PurePosixPath]:
    parts = PurePosixPath(template).parts
    if len(parts) < 3 or parts[0] != "sources" or any(part in {"", ".", ".."} for part in parts):
        raise TemplateSourceError("template source path is invalid")
    return parts[1], PurePosixPath(*parts[2:])


def _safe_source_file(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            item = current.lstat()
        except FileNotFoundError as error:
            raise TemplateSourceError("template source file is unavailable") from error
        if stat.S_ISLNK(item.st_mode):
            raise TemplateSourceError("template source files must not traverse symlinks")
    if not current.is_file():
        raise TemplateSourceError("template source file must be a regular file")
    return current
