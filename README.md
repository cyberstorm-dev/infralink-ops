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

`infralink-controller-registry-checkout fetch` is the matching controller
runnable. It receives the exact registry checkout, remote, ref, identity file,
and trust file, then returns only the detached resolved revision in a bounded
YAML envelope. It does not render or apply desired state.

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

`infralink-controller-config-consumers validate|activate` is the matching
public controller runnable for Compose services that consume those rendered
paths. The caller supplies an already-rendered deployment declaration, Compose
file, controller-owned config root, and changed relative paths. `validate` runs
only the validators for affected declared consumers. `activate` recreates only
affected consumers or services with changed or stale direct config-file binds.
It returns the selected consumer and service identifiers in a bounded YAML
envelope. It does not fetch Git, choose registry state, resolve secrets, or
infer environment-specific paths.

`infralink-controller-firewall render|verify` is the matching public firewall
runtime. It receives an explicit registry root, expected registry revision,
host UUID, and rendered Compose file; it validates the portable Infralink
firewall declaration, renders the owned nftables table, or verifies its
declared rules at runtime. It does not fetch Git, select a registry revision,
resolve secrets, or apply firewall state. It grants no implicit Tailnet,
Docker bridge, DNS, or container-egress access: every permitted listener is
declared. Controller-owned egress-SNAT realization remains a separate runtime;
declared container-egress support is tracked in #56.
