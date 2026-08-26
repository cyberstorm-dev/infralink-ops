import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

import infralink_ops.stable_regular_file as stable_regular_file
from infralink_ops.typed_artifact_materializer import (
    TypedArtifactMaterializationError,
    cli,
    materialize_v2_artifact_bindings,
)

HOST_ID = "11111111-1111-1111-1111-111111111111"


def _digest(body: bytes) -> str:
    return sha256(body).hexdigest()


def _commit(registry: Path) -> str:
    subprocess.run(["git", "init", "-q", str(registry)], check=True)
    subprocess.run(
        ["git", "-C", str(registry), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(registry), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(registry), "add", "."], check=True)
    subprocess.run(["git", "-C", str(registry), "commit", "-qm", "initial"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(registry), "rev-parse", "HEAD"], text=True
    ).strip()


def _write_catalog(registry: Path) -> Path:
    gatus = b"endpoints: []\n"
    overview = b'{"title":"Overview"}\n'
    detail = b'{"title":"Detail"}\n'
    rendered = registry / "operations" / "rendered"
    rendered.mkdir(parents=True)
    (rendered / "gatus.yml").write_bytes(gatus)
    (rendered / "overview.json").write_bytes(overview)
    (rendered / "detail.json").write_bytes(detail)

    artifact_slots = [
        {
            "id": "gatus-config",
            "component_id": "gatus",
            "kind": "file",
            "target": "gatus/config/config.yml",
            "mode": 0o640,
            "owner_uid": os.geteuid(),
            "owner_gid": os.getegid(),
            "consumer_id": "gatus",
            "lifecycle": "compose-recreate",
            "purpose": "Gatus configuration.",
        },
        {
            "id": "grafana-dashboards",
            "component_id": "grafana",
            "kind": "tree",
            "target": "grafana/dashboards",
            "mode": 0o640,
            "owner_uid": os.geteuid(),
            "owner_gid": os.getegid(),
            "consumer_id": "grafana",
            "lifecycle": "provider-poll",
            "purpose": "Grafana dashboards.",
        },
    ]
    document = {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "observability",
                "components": [
                    {"id": "gatus", "endpoints": []},
                    {"id": "grafana", "endpoints": []},
                ],
                "artifact_slots": artifact_slots,
            }
        ],
        "service_instances": [
            {
                "id": "observability",
                "host_id": HOST_ID,
                "profile_id": "observability",
                "components": [{"slot_id": "gatus"}, {"slot_id": "grafana"}],
                "artifact_bindings": [
                    {
                        "slot_id": "gatus-config",
                        "sources": [
                            {
                                "path": "operations/rendered/gatus.yml",
                                "sha256": _digest(gatus),
                            }
                        ],
                    },
                    {
                        "slot_id": "grafana-dashboards",
                        "sources": [
                            {
                                "path": "operations/rendered/overview.json",
                                "sha256": _digest(overview),
                                "relative_target": "core/overview.json",
                            },
                            {
                                "path": "operations/rendered/detail.json",
                                "sha256": _digest(detail),
                                "relative_target": "core/detail.json",
                            },
                        ],
                    },
                ],
            }
        ],
    }
    catalog = registry / "service-catalog" / "v2" / "observability.yml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return catalog


def _write_cross_profile_collision(registry: Path) -> Path:
    body = b'{"title":"Collision"}\n'
    source = registry / "operations" / "rendered" / "collision.json"
    source.write_bytes(body)
    document = {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "other-observability",
                "components": [{"id": "dashboard", "endpoints": []}],
                "artifact_slots": [
                    {
                        "id": "dashboard-tree",
                        "component_id": "dashboard",
                        "kind": "tree",
                        "target": "gatus",
                        "mode": 0o640,
                        "owner_uid": os.geteuid(),
                        "owner_gid": os.getegid(),
                        "consumer_id": "other-grafana",
                        "lifecycle": "provider-poll",
                        "purpose": "Deliberate collision fixture.",
                    }
                ],
            }
        ],
        "service_instances": [
            {
                "id": "other-observability",
                "host_id": HOST_ID,
                "profile_id": "other-observability",
                "components": [{"slot_id": "dashboard"}],
                "artifact_bindings": [
                    {
                        "slot_id": "dashboard-tree",
                        "sources": [
                            {
                                "path": "operations/rendered/collision.json",
                                "sha256": _digest(body),
                                "relative_target": "config",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    catalog = registry / "service-catalog" / "v2" / "collision.yml"
    catalog.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return catalog


def test_materializes_typed_file_and_tree_bindings_for_one_host(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    revision = _commit(registry)
    services = tmp_path / "services"

    result = materialize_v2_artifact_bindings(
        registry=registry,
        expected_revision=revision,
        host_id=HOST_ID,
        services_dir=services,
        source_paths=[catalog],
    )

    assert result.changed_paths == (
        "gatus/config/config.yml",
        "grafana/dashboards/core/detail.json",
        "grafana/dashboards/core/overview.json",
    )
    assert result.affected_consumers == ("gatus", "grafana")
    assert (services / "gatus" / "config" / "config.yml").read_text() == "endpoints: []\n"
    assert (services / "grafana" / "dashboards" / "core" / "overview.json").read_bytes() == (
        b'{"title":"Overview"}\n'
    )
    assert (services / "grafana" / "dashboards" / "core" / "detail.json").read_bytes() == (
        b'{"title":"Detail"}\n'
    )


def test_rejects_host_wide_overlapping_artifact_targets_before_writing(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    collision = _write_cross_profile_collision(registry)
    revision = _commit(registry)
    services = tmp_path / "services"

    with pytest.raises(TypedArtifactMaterializationError, match="artifact targets overlap"):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=services,
            source_paths=[catalog, collision],
        )

    assert not services.exists()


def test_preflights_broken_target_symlinks_before_writing_any_artifact(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    revision = _commit(registry)
    services = tmp_path / "services"
    broken_target = services / "grafana" / "dashboards" / "core" / "detail.json"
    broken_target.parent.mkdir(parents=True)
    broken_target.symlink_to(tmp_path / "missing.json")

    with pytest.raises(TypedArtifactMaterializationError, match="managed_destination_symlink"):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=services,
            source_paths=[catalog],
        )

    assert not (services / "gatus" / "config" / "config.yml").exists()


def test_cli_materializes_only_the_selected_registry_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    revision = _commit(registry)
    services = tmp_path / "services"

    status = cli(
        [
            "--registry",
            str(registry),
            "--registry-revision",
            revision,
            "--uuid",
            HOST_ID,
            "--services-dir",
            str(services),
            "--source",
            str(catalog.relative_to(registry)),
        ]
    )

    assert status == 0
    assert json.loads(capsys.readouterr().out) == {
        "affected_consumers": ["gatus", "grafana"],
        "changed_paths": [
            "gatus/config/config.yml",
            "grafana/dashboards/core/detail.json",
            "grafana/dashboards/core/overview.json",
        ],
    }


def test_rejects_catalog_sources_outside_selected_registry_before_writing(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    outside_catalog = tmp_path / "outside.yml"
    outside_catalog.write_text(catalog.read_text(encoding="utf-8"), encoding="utf-8")
    revision = _commit(registry)
    services = tmp_path / "services"

    with pytest.raises(
        TypedArtifactMaterializationError, match="V2 artifact source is unavailable"
    ):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=services,
            source_paths=[Path("../outside.yml")],
        )

    assert not services.exists()


def test_rejects_lexically_escaping_services_directory_before_writing(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    revision = _commit(registry)
    services = tmp_path / "services" / ".." / "outside"

    with pytest.raises(TypedArtifactMaterializationError, match="services directory is unsafe"):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=services,
            source_paths=[catalog],
        )

    assert not (tmp_path / "outside").exists()


def test_rejects_source_swapped_to_symlink_before_stable_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    source = registry / "operations" / "rendered" / "gatus.yml"
    outside = tmp_path / "outside.yml"
    outside.write_bytes(b"outside\n")
    revision = _commit(registry)
    original_open = stable_regular_file.os.open
    swapped = False

    def swap_source_before_open(name: str, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if name == source.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(stable_regular_file.os, "open", swap_source_before_open)

    with pytest.raises(
        TypedArtifactMaterializationError, match="declared artifact source is unavailable"
    ):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=tmp_path / "services",
            source_paths=[catalog],
        )

    assert not (tmp_path / "services").exists()


def test_rejects_catalog_swapped_to_symlink_before_stable_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry"
    catalog = _write_catalog(registry)
    outside_catalog = tmp_path / "outside.yml"
    outside_catalog.write_text(catalog.read_text(encoding="utf-8"), encoding="utf-8")
    revision = _commit(registry)
    original_open = stable_regular_file.os.open
    swapped = False

    def swap_catalog_before_open(name: str, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if name == catalog.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            catalog.unlink()
            catalog.symlink_to(outside_catalog)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(stable_regular_file.os, "open", swap_catalog_before_open)

    with pytest.raises(
        TypedArtifactMaterializationError, match="V2 artifact source is unavailable"
    ):
        materialize_v2_artifact_bindings(
            registry=registry,
            expected_revision=revision,
            host_id=HOST_ID,
            services_dir=tmp_path / "services",
            source_paths=[catalog],
        )

    assert not (tmp_path / "services").exists()
