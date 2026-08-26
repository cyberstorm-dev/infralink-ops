"""Materialize public Infralink V2 artifact bindings from one selected checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from infralink.observation import (
    ObservationV2Document,
    PlannedArtifactBinding,
    plan_v2_artifact_bindings,
)

from infralink_ops.artifact_target_install import ArtifactTargetError, install_artifact_body
from infralink_ops.registry_checkout import RegistryCheckoutError, verify_registry_revision
from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file


class TypedArtifactMaterializationError(ValueError):
    """Typed Registry artifact bindings cannot be materialized safely."""


@dataclass(frozen=True)
class TypedArtifactMaterializationResult:
    """Changed files and consumers affected by one materialization run."""

    changed_paths: tuple[str, ...]
    affected_consumers: tuple[str, ...]


@dataclass(frozen=True)
class _ArtifactWrite:
    target: Path
    relative_target: PurePosixPath
    body: bytes
    mode: int
    owner_uid: int
    owner_gid: int
    consumer_id: str


def materialize_v2_artifact_bindings(
    *,
    registry: Path,
    expected_revision: str,
    host_id: str,
    services_dir: Path,
    source_paths: Sequence[Path],
) -> TypedArtifactMaterializationResult:
    """Apply one host's declared V2 static artifacts from an exact checkout.

    This function does not fetch or select Registry state, resolve secrets, or
    activate consumers. The caller supplies the already selected revision and
    receives only the changed paths plus their declared consumers.
    """

    checkout = verify_registry_revision(registry, expected_revision=expected_revision)
    root = checkout.root
    services_root = _services_root(services_dir)
    paths = _validated_catalog_sources(root, source_paths)
    bindings = _project_stable_artifact_bindings(paths)
    writes = _plan_host_writes(root, services_root, host_id, bindings)
    _preflight_write_targets(services_root, writes)

    changed_paths: list[str] = []
    affected_consumers: list[str] = []
    for write in writes:
        _ensure_safe_directory(write.target.parent)
        try:
            changed = install_artifact_body(
                write.target,
                write.body,
                mode=write.mode,
                uid=write.owner_uid,
                gid=write.owner_gid,
            ).changed
        except ArtifactTargetError as error:
            raise TypedArtifactMaterializationError(str(error)) from error
        if changed:
            changed_paths.append(write.relative_target.as_posix())
            if write.consumer_id not in affected_consumers:
                affected_consumers.append(write.consumer_id)

    return TypedArtifactMaterializationResult(
        changed_paths=tuple(changed_paths), affected_consumers=tuple(affected_consumers)
    )


def cli(argv: list[str] | None = None) -> int:
    """Controller runnable for the typed artifact materialization primitive."""

    parser = argparse.ArgumentParser(prog="infralink-controller-artifact-bindings")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--registry-revision", required=True)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--services-dir", required=True, type=Path)
    parser.add_argument("--source", action="append", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = materialize_v2_artifact_bindings(
            registry=arguments.registry,
            expected_revision=arguments.registry_revision,
            host_id=arguments.uuid,
            services_dir=arguments.services_dir,
            source_paths=arguments.source,
        )
    except (RegistryCheckoutError, TypedArtifactMaterializationError) as error:
        print(f"controller artifact bindings: {error}", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "affected_consumers": list(result.affected_consumers),
                "changed_paths": list(result.changed_paths),
            },
            sort_keys=True,
        )
    )
    return 0


def _services_root(value: Path) -> Path:
    if not value.is_absolute() or _has_unsafe_path_component(value.parts[1:]):
        raise TypedArtifactMaterializationError("services directory is unsafe")
    return value


def _validated_catalog_sources(root: Path, values: Sequence[Path]) -> tuple[Path, ...]:
    if not values:
        raise TypedArtifactMaterializationError("at least one V2 artifact source is required")
    sources: list[Path] = []
    for value in values:
        if not value.is_absolute() and _has_unsafe_path_component(value.parts):
            raise TypedArtifactMaterializationError("V2 artifact source is unavailable")
        source = value if value.is_absolute() else root / value
        try:
            relative = source.relative_to(root)
        except ValueError:
            raise TypedArtifactMaterializationError("V2 artifact source is unavailable") from None
        if _has_unsafe_path_component(relative.parts):
            raise TypedArtifactMaterializationError("V2 artifact source is unavailable")
        sources.append(source)
    return tuple(sources)


def _project_stable_artifact_bindings(
    paths: Sequence[Path],
) -> tuple[PlannedArtifactBinding, ...]:
    documents: list[ObservationV2Document] = []
    for path in paths:
        try:
            body = read_stable_regular_file(path)
            value = yaml.safe_load(body)
            if not isinstance(value, dict):
                raise ValueError
            documents.append(ObservationV2Document.model_validate_json(json.dumps(value)))
        except (StableRegularFileError, ValueError, yaml.YAMLError) as error:
            raise TypedArtifactMaterializationError("V2 artifact source is unavailable") from error
    try:
        return tuple(plan_v2_artifact_bindings(documents))
    except ValueError as error:
        raise TypedArtifactMaterializationError("v2 artifact bindings are invalid") from error


def _plan_host_writes(
    root: Path,
    services_root: Path,
    host_id: str,
    bindings: Sequence[PlannedArtifactBinding],
) -> tuple[_ArtifactWrite, ...]:
    writes: list[_ArtifactWrite] = []
    for binding in bindings:
        if binding.host_id != host_id:
            continue
        slot = binding.slot
        for source in binding.sources:
            relative = PurePosixPath(slot.target)
            if source.relative_target is not None:
                relative /= PurePosixPath(source.relative_target)
            writes.append(
                _ArtifactWrite(
                    target=services_root.joinpath(*relative.parts),
                    relative_target=relative,
                    body=_read_exact_source(root, source.path, source.sha256),
                    mode=slot.mode,
                    owner_uid=slot.owner_uid,
                    owner_gid=slot.owner_gid,
                    consumer_id=slot.consumer_id,
                )
            )
    writes.sort(key=lambda item: item.relative_target.as_posix())
    _reject_overlapping_targets(writes)
    return tuple(writes)


def _read_exact_source(root: Path, relative_path: str, expected_digest: str) -> bytes:
    source = root.joinpath(*PurePosixPath(relative_path).parts)
    try:
        body = read_stable_regular_file(source)
    except StableRegularFileError:
        raise TypedArtifactMaterializationError("declared artifact source is unavailable")

    if hashlib.sha256(body).hexdigest() != expected_digest:
        raise TypedArtifactMaterializationError("declared artifact source digest mismatch")
    return body


def _has_unsafe_path_component(parts: Sequence[str]) -> bool:
    return any(part in {"", ".", ".."} for part in parts)


def _reject_overlapping_targets(writes: Sequence[_ArtifactWrite]) -> None:
    for index, left in enumerate(writes):
        for right in writes[index + 1 :]:
            if _paths_overlap(left.relative_target, right.relative_target):
                raise TypedArtifactMaterializationError("declared artifact targets overlap")


def _paths_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return (
        left.parts == right.parts[: len(left.parts)]
        or right.parts == left.parts[: len(right.parts)]
    )


def _preflight_write_targets(services_root: Path, writes: Sequence[_ArtifactWrite]) -> None:
    for write in writes:
        _preflight_safe_path(services_root, write.target.parent)
        if not write.target.exists() and not write.target.is_symlink():
            continue
        details = write.target.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise TypedArtifactMaterializationError("managed_destination_symlink")
        if stat.S_ISREG(details.st_mode):
            continue
        if stat.S_ISDIR(details.st_mode) and not any(write.target.iterdir()):
            continue
        raise TypedArtifactMaterializationError("managed_destination_nonempty_directory")


def _preflight_safe_path(root: Path, destination: Path) -> None:
    try:
        relative = destination.relative_to(root)
    except ValueError as error:
        raise TypedArtifactMaterializationError(
            "artifact target escapes services directory"
        ) from error
    current = root
    if current.exists() and current.is_symlink():
        raise TypedArtifactMaterializationError("services directory is a symlink")
    for part in relative.parts:
        current = current / part
        if not current.exists() and not current.is_symlink():
            return
        if current.is_symlink() or not current.is_dir():
            raise TypedArtifactMaterializationError("artifact target parent is unsafe")


def _ensure_safe_directory(path: Path) -> None:
    if not path.is_absolute():
        raise TypedArtifactMaterializationError("artifact target parent is unsafe")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                successor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=descriptor)
                successor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise TypedArtifactMaterializationError(
                    "artifact target parent is unsafe"
                ) from error
            os.close(descriptor)
            descriptor = successor
    except OSError as error:
        raise TypedArtifactMaterializationError("artifact target parent is unsafe") from error
    finally:
        os.close(descriptor)
