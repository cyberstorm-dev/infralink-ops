"""Strict checkout of an already-configured Infralink registry."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RegistryCheckoutError(RuntimeError):
    """The configured registry checkout cannot be used safely."""


@dataclass(frozen=True)
class RegistryCheckout:
    """Verified registry checkout evidence returned after a fetch."""

    root: Path
    revision: str


def verify_registry_revision(registry_root: Path, *, expected_revision: str) -> RegistryCheckout:
    """Verify an existing checkout without fetching or selecting a revision."""

    root = registry_root.resolve()
    _require_existing_checkout(root)
    revision = _git(root, "rev-parse", "HEAD")
    if revision != expected_revision:
        raise RegistryCheckoutError(
            f"registry revision mismatch: expected {expected_revision}, checkout has {revision}"
        )
    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=all",
    )
    if status:
        raise RegistryCheckoutError("registry checkout must be clean")
    return RegistryCheckout(root=root, revision=revision)


def fetch_configured_registry(
    registry_root: Path,
    *,
    configured_remote: str,
    configured_ref: str,
    identity_file: Path,
    known_hosts_file: Path,
) -> RegistryCheckout:
    """Converge one existing registry runtime cache to its declared ref."""

    root = registry_root.resolve()
    if not configured_remote:
        raise RegistryCheckoutError("configured registry remote is missing")
    if not configured_ref:
        raise RegistryCheckoutError("configured registry ref is missing")
    _require_existing_checkout(root)
    _require_readable_file(identity_file, "registry identity")
    _require_readable_file(known_hosts_file, "registry trust file")

    origin = _git(root, "remote", "get-url", "origin")
    if origin != configured_remote:
        raise RegistryCheckoutError("registry checkout origin does not match declared remote")

    git_ssh_command = " ".join(
        (
            "ssh",
            "-i",
            shlex.quote(str(identity_file)),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={shlex.quote(str(known_hosts_file))}",
        )
    )
    _git(
        root,
        "fetch",
        "--prune",
        "--no-recurse-submodules",
        "origin",
        configured_ref,
        extra_environment={"GIT_SSH_COMMAND": git_ssh_command},
    )
    _git(root, "reset", "--hard", "FETCH_HEAD")
    _git(root, "clean", "-ffd")
    _git(root, "checkout", "--detach", "FETCH_HEAD")
    revision = _git(root, "rev-parse", "HEAD")
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise RegistryCheckoutError("registry checkout did not resolve to a full SHA-1 revision")
    try:
        return verify_registry_revision(root, expected_revision=revision)
    except RegistryCheckoutError as error:
        raise RegistryCheckoutError("registry checkout could not be converged") from error


def _require_existing_checkout(root: Path) -> None:
    if not root.is_dir():
        raise RegistryCheckoutError("registry checkout is missing")
    try:
        top_level = _git(root, "rev-parse", "--show-toplevel")
    except RegistryCheckoutError as error:
        raise RegistryCheckoutError("registry checkout is invalid") from error
    if Path(top_level).resolve() != root:
        raise RegistryCheckoutError("registry checkout root is not the Git top-level")


def _require_readable_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise RegistryCheckoutError(f"{description} is missing")
    if not os.access(path, os.R_OK):
        raise RegistryCheckoutError(f"{description} is unreadable")


def _git(root: Path, *arguments: str, extra_environment: dict[str, str] | None = None) -> str:
    environment = os.environ.copy()
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise RegistryCheckoutError("configured registry Git operation failed")
    return completed.stdout.strip()
