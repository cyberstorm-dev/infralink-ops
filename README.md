# Infralink Ops

Operational support images consumed by registry declarations. The first image adds a
Gitleaks receive gate to Gitea. It uses Git's template directory for newly created
repositories and a registry-declared synchronizer for existing repositories.

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
