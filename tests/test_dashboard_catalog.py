import hashlib
import json
import subprocess

import pytest

from infralink_ops.dashboards import load_registry_dashboards


def _registry_with_dashboard_catalog(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)

    catalog_directory = tmp_path / "service-catalog" / "dashboards"
    catalog_directory.mkdir(parents=True)
    asset = catalog_directory / "nginx-vts.json"
    asset.write_text(
        json.dumps(
            {
                "title": "Upstream NGINX",
                "panels": [{"datasource": "${DS_PROMETHEUS}"}],
                "templating": {"list": [{"datasource": "${DS_PROMETHEUS}"}]},
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (tmp_path / "service-catalog" / "dashboards.yml").write_text(
        f"""\
schema_version: infralink.dashboard-catalog/v1
dashboards:
  - id: nginx-vts
    profile_id: nginx-vts-exporter
    title: NGINX VTS Stats
    grafana:
      uid: nginx-vts
      datasource_input: DS_PROMETHEUS
      asset: service-catalog/dashboards/nginx-vts.json
      upstream:
        dashboard_id: 2949
        revision: 1
        sha256: {digest}
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "dashboard catalog"], check=True)
    revision = _revision(tmp_path)
    return revision, asset


def _revision(root) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_loads_verified_registry_asset_and_stamps_catalog_uid_and_datasource(tmp_path) -> None:
    revision, _ = _registry_with_dashboard_catalog(tmp_path)

    dashboards = load_registry_dashboards(
        tmp_path,
        expected_revision=revision,
        datasource="prometheus-primary",
    )

    assert dashboards == (
        {
            "id": "nginx-vts",
            "profile_id": "nginx-vts-exporter",
            "dashboard": {
                "title": "Upstream NGINX",
                "panels": [{"datasource": "prometheus-primary"}],
                "uid": "nginx-vts",
                "templating": {"list": [{"datasource": "prometheus-primary"}]},
            },
        },
    )


def test_rejects_nested_directory_inside_registry_checkout(tmp_path) -> None:
    revision, _ = _registry_with_dashboard_catalog(tmp_path)
    nested_directory = tmp_path / "service-catalog"

    with pytest.raises(ValueError, match="registry root must be the Git checkout top-level"):
        load_registry_dashboards(
            nested_directory,
            expected_revision=revision,
            datasource="prometheus-primary",
        )


def test_rejects_dashboard_asset_with_mismatched_catalog_sha(tmp_path) -> None:
    revision, asset = _registry_with_dashboard_catalog(tmp_path)
    asset.write_text('{"title": "tampered"}', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", str(asset)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "tampered asset"], check=True)

    with pytest.raises(ValueError, match="dashboard asset sha256 mismatch"):
        load_registry_dashboards(
            tmp_path,
            expected_revision=_revision(tmp_path),
            datasource="prometheus-primary",
        )

    assert _revision(tmp_path) != revision


def test_rejects_dashboard_catalog_without_asset_sha(tmp_path) -> None:
    revision, _ = _registry_with_dashboard_catalog(tmp_path)
    catalog = tmp_path / "service-catalog" / "dashboards.yml"
    catalog.write_text(
        "\n".join(
            line
            for line in catalog.read_text(encoding="utf-8").splitlines()
            if "sha256:" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", str(catalog)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "missing asset digest"], check=True
    )

    with pytest.raises(ValueError, match="requires non-empty sha256"):
        load_registry_dashboards(
            tmp_path,
            expected_revision=_revision(tmp_path),
            datasource="prometheus-primary",
        )

    assert _revision(tmp_path) != revision


def test_rejects_dashboard_asset_outside_verified_registry_root(tmp_path) -> None:
    revision, _ = _registry_with_dashboard_catalog(tmp_path)
    catalog = tmp_path / "service-catalog" / "dashboards.yml"
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace(
            "service-catalog/dashboards/nginx-vts.json", "../outside.json"
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", str(catalog)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "invalid asset path"], check=True)

    with pytest.raises(ValueError, match="dashboard asset must exist below registry root"):
        load_registry_dashboards(
            tmp_path,
            expected_revision=_revision(tmp_path),
            datasource="prometheus-primary",
        )

    assert _revision(tmp_path) != revision
