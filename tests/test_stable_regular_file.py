import os
from pathlib import Path
from unittest.mock import patch

import pytest

from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file


def test_reads_a_regular_file_without_following_path_components(tmp_path: Path) -> None:
    source = tmp_path / "registry" / "operations" / "config.yml"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"configured: true\n")

    assert read_stable_regular_file(source) == b"configured: true\n"


def test_rejects_symlinked_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yml"
    outside.write_bytes(b"retain\n")
    source = tmp_path / "registry" / "config.yml"
    source.parent.mkdir()
    source.symlink_to(outside)

    with pytest.raises(StableRegularFileError, match="stable regular file"):
        read_stable_regular_file(source)


def test_rejects_symlinked_parent_without_reading_outside(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.yml").write_bytes(b"retain\n")
    source_root = tmp_path / "registry"
    source_root.mkdir()
    (source_root / "operations").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StableRegularFileError, match="path is unsafe"):
        read_stable_regular_file(source_root / "operations" / "config.yml")


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    source = tmp_path / "registry" / ".." / "outside.yml"

    with pytest.raises(StableRegularFileError, match="path is unsafe"):
        read_stable_regular_file(source)


def test_rejects_file_changed_while_reading(tmp_path: Path) -> None:
    source = tmp_path / "registry" / "config.yml"
    source.parent.mkdir()
    source.write_bytes(b"original\n")
    original_read = os.read
    reads = 0

    def read_then_change(descriptor: int, size: int) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 2:
            return b""
        chunk = original_read(descriptor, size)
        source.write_bytes(b"changed-after-read\n")
        return chunk

    with patch("infralink_ops.stable_regular_file.os.read", side_effect=read_then_change):
        with pytest.raises(StableRegularFileError, match="stable regular file"):
            read_stable_regular_file(source)
