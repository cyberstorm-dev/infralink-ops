"""Revision-verified registry sources exposed to declared Jinja templates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jinja2 import BaseLoader, TemplateNotFound

from .config_trees import verify_declared_registry_source_directory
from .stable_regular_file import StableRegularFileError, read_stable_regular_file


class TemplateSourceError(ValueError):
    """A registry-declared template source is unavailable or unsafe."""


@dataclass(frozen=True)
class TemplateSource:
    """One named template directory within the selected registry revision."""

    id: str
    root: Path
    files: frozenset[PurePosixPath]


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
            resolved = verify_declared_registry_source_directory(
                registry, expected_revision=expected_revision, source=source_path
            )
        except ValueError as error:
            raise TemplateSourceError("template source directory is invalid") from error
        ids.add(source_id)
        sources.append(
            TemplateSource(id=source_id, root=resolved.root, files=frozenset(resolved.files))
        )
    return tuple(sources)


class DeclaredTemplateSourceLoader(BaseLoader):
    """Resolve only the ``sources/<id>/...`` aliases declared for one host."""

    def __init__(self, sources: tuple[TemplateSource, ...]) -> None:
        self._sources = {source.id: source for source in sources}

    def get_source(self, environment: object, template: str):  # type: ignore[override]
        if not template.startswith("sources/"):
            raise TemplateNotFound(template)
        source_id, relative = _source_template_name(template)
        source = self._sources.get(source_id)
        if source is None:
            raise TemplateSourceError("template source is not declared")
        if relative not in source.files:
            raise TemplateSourceError("template source file is not declared")
        try:
            body = read_stable_regular_file(source.root / Path(*relative.parts)).decode("utf-8")
        except (StableRegularFileError, UnicodeDecodeError) as error:
            raise TemplateSourceError("template source file is unavailable") from error
        # Revalidate on the next load rather than consulting mutable path metadata.
        return body, str(source.root / Path(*relative.parts)), lambda: False


def _valid_id(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value) and value.replace("-", "").replace("_", "").isalnum()
    )


def _source_template_name(template: str) -> tuple[str, PurePosixPath]:
    parts = PurePosixPath(template).parts
    if len(parts) < 3 or parts[0] != "sources" or any(part in {"", ".", ".."} for part in parts):
        raise TemplateSourceError("template source path is invalid")
    return parts[1], PurePosixPath(*parts[2:])
