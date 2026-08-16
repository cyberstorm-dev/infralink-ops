#!/bin/sh
set -eu

registry_dir="$(mktemp -d)"
mkdir -p "$registry_dir/observability"

git init -q "$registry_dir"
git -C "$registry_dir" config user.email test@example.invalid
git -C "$registry_dir" config user.name "Runtime test"

cat >"$registry_dir/observability/views.yml" <<'YAML'
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
YAML

git -C "$registry_dir" add observability/views.yml
git -C "$registry_dir" commit -qm "runtime observation fixture"
revision="$(git -C "$registry_dir" rev-parse HEAD)"

infralink-ops \
  --registry-root "$registry_dir" \
  --observation-directory observability \
  --expected-revision "$revision" \
  | grep -q "registry_revision: $revision"
