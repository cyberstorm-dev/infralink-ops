import subprocess
from datetime import datetime, timezone

import pytest

from infralink_ops.observation import project_registry_observation, project_registry_v2_metrics


def _registry(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source = tmp_path / "observability"
    source.mkdir()
    (source / "views.yml").write_text(
        """\
schema_version: infralink.observation/v1
service_profiles:
  - id: nginx
    endpoints:
      - {id: metrics, protocol: http, port: 9113}
    metrics:
      - {id: metrics, endpoint_id: metrics, evaluator: prometheus-scrape}
hosts:
  - {id: 11111111-1111-4111-8111-111111111111}
service_instances:
  - id: nginx
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: nginx
observation_backends:
  - {id: prometheus, kind: metrics, backend_ref: central-prometheus}
datasource_bindings:
  - {id: primary-metrics, observation_backend_id: prometheus, datasource_ref: prometheus}
operations_views:
  - id: nginx
    purpose: Fleet NGINX metrics.
    kind: profile_metrics
    metric_profile_id: nginx
    datasource_binding_id: primary-metrics
    sections: []
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "observation"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return source, revision


def test_projects_only_explicit_observation_directory_at_checkout_revision(tmp_path) -> None:
    source, revision = _registry(tmp_path)

    result = project_registry_observation(
        tmp_path,
        observation_directory="observability",
        expected_revision=revision,
        as_of=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )

    assert result.plan.registry_revision == revision
    assert result.plan.operations_views[0].id == "nginx"
    assert result.plan.operations_views[0].service_ids == (
        "11111111-1111-4111-8111-111111111111/nginx",
    )


def test_rejects_a_stale_registry_checkout(tmp_path) -> None:
    _, revision = _registry(tmp_path)

    with pytest.raises(ValueError, match="registry revision mismatch"):
        project_registry_observation(
            tmp_path,
            observation_directory="observability",
            expected_revision="0" * 40,
            as_of=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )

    assert revision != "0" * 40


def test_projects_v2_metrics_from_only_the_verified_catalog_directory(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source = tmp_path / "service-catalog" / "v2"
    source.mkdir(parents=True)
    (source / "nginx.yml").write_text(
        """schema_version: infralink.observation/v2
service_profiles:
  - id: nginx
    components:
      - id: exporter
        endpoints:
          - {id: metrics, protocol: http, port: 9113}
        metrics:
          - id: requests
            endpoint_id: metrics
            path: /metrics
            metric_name: nginx_http_requests_total
            unit: requests
service_instances:
  - id: nginx
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: nginx
    components:
      - slot_id: exporter
        endpoint_bindings:
          - {endpoint_id: metrics, address: 100.64.0.10}
""",
        encoding="ascii",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "v2 metrics"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = project_registry_v2_metrics(tmp_path, expected_revision=revision)

    assert [metric.id for metric in result.metrics] == [
        "11111111-1111-4111-8111-111111111111/nginx/exporter/requests"
    ]
    assert result.metrics[0].prometheus.address == "100.64.0.10"


def test_rejects_a_nested_registry_directory_for_v2_metrics(tmp_path) -> None:
    source, revision = _registry(tmp_path)

    with pytest.raises(ValueError, match="registry root must be the Git checkout top-level"):
        project_registry_v2_metrics(source, expected_revision=revision)
