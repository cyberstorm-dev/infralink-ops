# Infralink Ops

`infralink-controller-images retain-and-prune` is the controller-runtime
primitive for bounded Docker image retention. It accepts the selected immutable
image reference and returns a YAML/HATEOAS envelope; cache tags are never used
to select desired state.

`infralink-controller-metrics publish-success|publish-failure` is the
controller-runtime primitive for atomically publishing the existing
node-exporter convergence textfile. It accepts explicit revision/time inputs,
returns a YAML/HATEOAS envelope, and does not inspect a registry or select a
desired state.

Operational support images consumed by registry declarations. The first image adds a
Gitleaks receive gate to Gitea. It uses Git's template directory for newly created
repositories and a registry-declared synchronizer for existing repositories.

`infralink_ops.controller_adapter.invoke_controller_adapter` is the typed
runtime boundary for environment adapters. It accepts a fixed argv and a public
Infralink request contract, then returns only a validated, revision-matched
adapter result. Environment-specific rendering and provider behavior remains
outside this package.

`infralink_ops.registry_checkout.fetch_configured_registry` is the sole
checkout primitive for controller runtimes. It only fetches a declared ref into
an existing clean checkout after validating its exact origin and explicit SSH
identity/trust files; it never clones, rewrites a remote, or discovers trust.

## Registry projections

`infralink_ops.materialize_config_tree` projects one registry-declared static
configuration tree into a controller-owned path beneath `/opt/services/config`.
It requires the registry checkout selected by the caller's normal deployment path
and the exact Git revision expected for that checkout. It validates the source and
target trees before writing, atomically replaces changed files, and removes stale
files only below the declared target.

The package does not fetch Git, select a revision, store a plan, or invoke a
service consumer. A controller supplies those decisions and may pass the returned
changed paths to its existing consumer executor.
