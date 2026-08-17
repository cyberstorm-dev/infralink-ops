# AGENTS.md

This repository is public operational runtime code for Infralink projections and
support images. Keep changes small, reviewable, and free of private operational
facts.

## Canonical Commands

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

Image checks:

```bash
docker build --tag infralink-ops-gitea-gitleaks:test .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-gitea-gitleaks:test /tests/receive-gate.sh
docker build --tag infralink-ops-runtime:test -f Dockerfile.ops .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-runtime:test /tests/runtime-image.sh
```

## Change Boundaries

- Do not publish releases, create tags, merge PRs, or change repository
  visibility.
- Do not copy private host inventories, hostnames, live IP addresses, secret
  names, project identifiers, deployment paths, or operational runbook facts.
- Rewrite ambiguous private-source ideas as sanitized public requirements before
  implementation.
- Keep authored prose, validation tooling, generated output, and runtime changes
  in separate Conventional Commits when practical.
- Runtime changes must preserve revision pinning, path containment, read-only
  projection behavior, and digest checks unless the issue explicitly changes
  that contract.

## Expected Repo Shape

- `src/infralink_ops/` contains Python runtime helpers and CLI entrypoints.
- `gitea-hooks/` contains receive-gate hooks copied into the Gitea support
  image.
- `tests/` contains Python and shell validation, including public-data boundary
  checks.
- `Dockerfile` builds the Gitea/Gitleaks support image.
- `Dockerfile.ops` builds the Python runtime image.
