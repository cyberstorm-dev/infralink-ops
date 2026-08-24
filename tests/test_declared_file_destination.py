from pathlib import Path

import pytest

from infralink_ops.declared_file_destination import (
    DeclaredFileDestinationError,
    classify_declared_file_destination,
    repair_empty_declared_file_destination,
)


def test_rejects_unsafe_destination_shapes(tmp_path: Path) -> None:
    root = tmp_path / "config"
    root.mkdir()
    (root / "symlink-parent").symlink_to(tmp_path)
    (root / "file-parent").write_text("not a directory\n", encoding="utf-8")
    (root / "symlink-leaf").symlink_to(tmp_path / "missing")
    nonempty = root / "nonempty"
    nonempty.mkdir()
    (nonempty / "managed.yml").write_text("present\n", encoding="utf-8")

    cases = [
        (Path("../escape.yml"), "invalid-relative-path"),
        (Path("symlink-parent/managed.yml"), "managed_destination_parent_symlink"),
        (Path("file-parent/managed.yml"), "managed_destination_parent_not_directory"),
        (Path("symlink-leaf"), "managed_destination_symlink"),
        (Path("nonempty"), "managed_destination_nonempty_directory"),
    ]
    for relative, reason in cases:
        with pytest.raises(DeclaredFileDestinationError, match=reason):
            classify_declared_file_destination(root, relative)


def test_repairs_only_an_explicit_empty_directory_leaf(tmp_path: Path) -> None:
    root = tmp_path / "config"
    destination = root / "nginx" / "nginx.conf"
    destination.mkdir(parents=True)

    repaired = repair_empty_declared_file_destination(root, Path("nginx/nginx.conf"))

    assert repaired == destination
    assert not destination.exists()
    assert repaired.parent.is_dir()
