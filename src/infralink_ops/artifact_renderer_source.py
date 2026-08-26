"""Verify immutable, authoring-only artifact renderer source pins."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file

SCHEMA_VERSION = "infralink.ops.artifact-renderer-source/v1"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class ArtifactRendererSourceError(ValueError):
    """An artifact renderer source declaration or checkout is invalid."""


@dataclass(frozen=True)
class ArtifactRendererSource:
    """One immutable renderer input for authoring and CI artifact derivation."""

    repository: str
    revision: str
    lock_digest: str


def load_artifact_renderer_source(lock_path: Path) -> ArtifactRendererSource:
    """Load a strict immutable renderer source declaration from one regular file."""

    try:
        body = read_stable_regular_file(lock_path)
        document = yaml.safe_load(body)
    except (OSError, StableRegularFileError, yaml.YAMLError) as error:
        raise ArtifactRendererSourceError("renderer source declaration is unavailable") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "repository",
        "revision",
    }:
        raise ArtifactRendererSourceError("renderer source declaration is invalid")
    repository = document["repository"]
    revision = document["revision"]
    if (
        document["schema_version"] != SCHEMA_VERSION
        or not isinstance(repository, str)
        or not repository
        or not isinstance(revision, str)
        or _SHA1.fullmatch(revision) is None
    ):
        raise ArtifactRendererSourceError("renderer source declaration is invalid")
    return ArtifactRendererSource(
        repository=repository,
        revision=revision,
        lock_digest=hashlib.sha256(body).hexdigest(),
    )


def verify_artifact_renderer_checkout(source: ArtifactRendererSource, checkout: Path) -> Path:
    """Return a clean checkout that exactly matches the declared renderer source.

    This validates an already supplied checkout. It never fetches, selects a
    controller version, or writes registry or host state.
    """

    root = checkout.resolve()
    if not root.is_dir() or _git(root, "rev-parse", "--show-toplevel") != str(root):
        raise ArtifactRendererSourceError("renderer checkout is unavailable")
    if _git(root, "rev-parse", "HEAD") != source.revision:
        raise ArtifactRendererSourceError("renderer checkout revision does not match declaration")
    if _git(root, "config", "--get", "remote.origin.url") != source.repository:
        raise ArtifactRendererSourceError("renderer checkout repository does not match declaration")
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"):
        raise ArtifactRendererSourceError("renderer checkout must be clean")
    return root


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ArtifactRendererSourceError("renderer checkout is unavailable") from error
    if completed.returncode != 0:
        raise ArtifactRendererSourceError("renderer checkout is unavailable")
    return completed.stdout.strip()
