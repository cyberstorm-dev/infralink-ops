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


def test_rejects_target_directory_replaced_during_empty_directory_repair(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.mkdir(parents=True)
    replaced = tmp_path / "replaced"
    original_listdir = os.listdir

    def list_then_replace(descriptor: int) -> list[str]:
        entries = original_listdir(descriptor)
        target.rename(replaced)
        target.mkdir()
        return entries

    with patch("infralink_ops.artifact_target_install.os.listdir", side_effect=list_then_replace):
        with pytest.raises(ArtifactTargetError, match="target changed during repair"):
            install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert target.is_dir()
    assert replaced.is_dir()


def test_rejects_target_changed_while_reading_an_exact_noop(tmp_path: Path) -> None:
    target = tmp_path / "mounted" / "config.yml"
    target.parent.mkdir()
    target.write_bytes(b"new\n")
    target.chmod(0o640)
    original_read = os.read
    reads = 0

    def read_then_change(descriptor: int, size: int) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 2:
            return b""
        chunk = original_read(descriptor, size)
        target.write_bytes(b"changed-after-read\n")
        return chunk

    with patch("infralink_ops.artifact_target_install.os.read", side_effect=read_then_change):
        with pytest.raises(ArtifactTargetError, match="target inspection failed"):
            install_artifact_body(target, b"new\n", mode=0o640, uid=os.geteuid(), gid=os.getegid())

    assert target.read_bytes() == b"changed-after-read\n"


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
