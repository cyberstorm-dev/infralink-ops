import os
import subprocess
from pathlib import Path

import pytest

from infralink_ops.config_trees import materialize_config_tree, preflight_config_trees

DECLARATION = {
    "source": "catalog/irc/static",
    "target": "/opt/services/config/irc/static",
    "file_mode": "0640",
    "directory_mode": "0750",
    "owner_uid": 0,
    "owner_gid": 0,
}


def registry_checkout(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    root = tmp_path / "registry"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    default_files = {"catalog/irc/static/modules.conf": "module = core\n"}
    for relative_path, content in (files or default_files).items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "registry"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return root, revision


def test_rejects_traversal_and_symlink_sources_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    declaration = {**DECLARATION, "source": "../outside"}

    with pytest.raises(ValueError, match="source must be a directory below registry root"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=declaration,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()

    (root / "catalog" / "link").symlink_to(root / "catalog" / "irc" / "static")
    subprocess.run(["git", "-C", str(root), "add", "catalog/link"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "symlink"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    declaration = {**DECLARATION, "source": "catalog/link"}
    with pytest.raises(ValueError, match="source must not be a symlink"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=declaration,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_rejects_noncanonical_targets_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    declaration = {**DECLARATION, "target": "/etc/infralink"}

    with pytest.raises(ValueError, match="target must be below /opt/services/config"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=declaration,
            services_root=services_root,
        )

    assert not services_root.exists()


def test_rejects_special_permission_bits_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    declaration = {**DECLARATION, "file_mode": "4755"}

    with pytest.raises(
        ValueError, match="file_mode must be a four-digit octal string without special bits"
    ):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=declaration,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_requires_declared_ownership_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    declaration = {key: value for key, value in DECLARATION.items() if key != "owner_gid"}

    with pytest.raises(ValueError, match="owner_gid must be a non-negative integer"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=declaration,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_rejects_a_dirty_registry_checkout_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    (root / "catalog" / "irc" / "static" / "modules.conf").write_text("uncommitted\n")

    with pytest.raises(ValueError, match="registry checkout must be clean"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=DECLARATION,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_rejects_ignored_source_content_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    (root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "ignore local files"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (root / "catalog" / "irc" / "static" / "rogue.local").write_text(
        "not registry content\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="source tree must match tracked registry content"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=DECLARATION,
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_rejects_intermediate_symlink_source_before_target_mutation(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    (root / "catalog" / "link").symlink_to(root / "catalog" / "irc", target_is_directory=True)
    subprocess.run(["git", "-C", str(root), "add", "catalog/link"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "intermediate source symlink"],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="source must not traverse a symlink"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration={**DECLARATION, "source": "catalog/link/static"},
            services_root=services_root,
        )

    assert not (services_root / "config" / "irc" / "static").exists()


def test_sync_updates_nested_files_removes_stale_entries_and_is_idempotent(tmp_path: Path) -> None:
    root, revision = registry_checkout(
        tmp_path,
        {"catalog/irc/static/a/base.conf": "new\n", "catalog/irc/static/b/extra.conf": "ok\n"},
    )
    services_root = tmp_path / "services"
    target = services_root / "config" / "irc" / "static"
    (target / "a").mkdir(parents=True)
    (target / "a" / "base.conf").write_text("old\n", encoding="utf-8")
    (target / "stale.conf").write_text("remove\n", encoding="utf-8")

    first = materialize_config_tree(
        root,
        expected_revision=revision,
        declaration=DECLARATION,
        services_root=services_root,
    )
    second = materialize_config_tree(
        root,
        expected_revision=revision,
        declaration=DECLARATION,
        services_root=services_root,
    )

    assert (target / "a" / "base.conf").read_text(encoding="utf-8") == "new\n"
    assert (target / "b" / "extra.conf").read_text(encoding="utf-8") == "ok\n"
    assert not (target / "stale.conf").exists()
    assert first.changed_paths == (
        "irc/static/a/base.conf",
        "irc/static/b/extra.conf",
        "irc/static/stale.conf",
    )
    assert second.changed_paths == ()
    assert os.stat(target / "a" / "base.conf").st_mode & 0o777 == 0o640
    assert os.stat(target / "b").st_mode & 0o777 == 0o750


def test_rejects_target_type_conflicts_before_any_write(tmp_path: Path) -> None:
    root, revision = registry_checkout(
        tmp_path,
        {"catalog/irc/static/a/base.conf": "new\n", "catalog/irc/static/b/extra.conf": "ok\n"},
    )
    services_root = tmp_path / "services"
    target = services_root / "config" / "irc" / "static"
    target.mkdir(parents=True)
    (target / "a").write_text("conflicting file\n", encoding="utf-8")

    with pytest.raises(ValueError, match="target type conflict"):
        materialize_config_tree(
            root,
            expected_revision=revision,
            declaration=DECLARATION,
            services_root=services_root,
        )

    assert (target / "a").read_text(encoding="utf-8") == "conflicting file\n"
    assert not (target / "b").exists()


def test_preflight_rejects_overlapping_declared_targets_before_any_write(tmp_path: Path) -> None:
    root, revision = registry_checkout(
        tmp_path,
        {
            "catalog/irc/static/modules.conf": "module = core\n",
            "catalog/irc/tls/server.crt": "certificate\n",
        },
    )
    services_root = tmp_path / "services"
    declarations = (
        DECLARATION,
        {
            **DECLARATION,
            "source": "catalog/irc/tls",
            "target": "/opt/services/config/irc/static/tls",
        },
    )

    with pytest.raises(ValueError, match="declared config tree targets must not overlap"):
        preflight_config_trees(
            root,
            expected_revision=revision,
            declarations=declarations,
            services_root=services_root,
        )

    assert not services_root.exists()


def test_preflight_rejects_a_later_invalid_declaration_before_any_write(tmp_path: Path) -> None:
    root, revision = registry_checkout(tmp_path)
    services_root = tmp_path / "services"
    declarations = (DECLARATION, {**DECLARATION, "source": "../outside"})

    with pytest.raises(ValueError, match="source must be a directory below registry root"):
        preflight_config_trees(
            root,
            expected_revision=revision,
            declarations=declarations,
            services_root=services_root,
        )

    assert not services_root.exists()


def test_public_config_tree_api_is_importable() -> None:
    from infralink_ops import ConfigTreeResult
    from infralink_ops import materialize_config_tree as public_materialize
    from infralink_ops import preflight_config_trees as public_preflight

    assert ConfigTreeResult.__name__ == "ConfigTreeResult"
    assert callable(public_materialize)
    assert callable(public_preflight)
