import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from infralink_ops.artifact_target_install import (
    ArtifactTargetDurabilityUncertainError,
    ArtifactTargetError,
    install_artifact_body,
)


def test_installs_bytes_atomically_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.parent.mkdir()
    target.write_bytes(b"old\n")

    first = install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())
    second = install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert first.changed is True
    assert second.changed is False
    assert target.read_bytes() == b"new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.parametrize("kind", ["symlink", "nonempty_directory"])
def test_rejects_unsafe_target_without_mutation(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.parent.mkdir()
    if kind == "symlink":
        outside = tmp_path / "outside.yml"
        outside.write_bytes(b"retain\n")
        target.symlink_to(outside)
    else:
        target.mkdir()
        (target / "retain").write_bytes(b"retain\n")

    with pytest.raises(ArtifactTargetError):
        install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())


def test_repairs_only_an_empty_target_directory(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.mkdir(parents=True)

    result = install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert result.changed is True
    assert target.read_bytes() == b"new\n"


def test_rejects_parent_traversal_without_writing_outside_target(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / ".." / "outside.yml"

    with pytest.raises(ArtifactTargetError, match="directory is unsafe"):
        install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert not (tmp_path / "outside.yml").exists()


def test_reports_durability_uncertainty_after_target_becomes_visible(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.parent.mkdir()

    with patch(
        "infralink_ops.artifact_target_install.os.fsync", side_effect=[None, OSError("full")]
    ):
        with pytest.raises(ArtifactTargetDurabilityUncertainError, match="durability uncertain"):
            install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert target.read_bytes() == b"new\n"


def test_retry_syncs_an_unchanged_target_after_durability_uncertainty(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.parent.mkdir()

    with patch(
        "infralink_ops.artifact_target_install.os.fsync",
        side_effect=[None, OSError("full"), None],
    ) as sync:
        with pytest.raises(ArtifactTargetDurabilityUncertainError):
            install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

        result = install_artifact_body(
            target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid()
        )

    assert result.changed is False
    assert sync.call_count == 3
