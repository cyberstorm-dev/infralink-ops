# Contributing

## Setup

Use Python 3.12 for the full local validation pass:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The package supports Python `>=3.10`; keep code compatible with Python 3.10+
unless an accepted issue changes that constraint. On macOS with Homebrew,
`/opt/homebrew/bin/python3.12` is one possible installation path, but use the
portable `python3.12` command in docs and scripts.

## Validation

Run the Python checks before opening a PR:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

Run image checks when touching `Dockerfile`, `Dockerfile.ops`, `gitea-hooks/`,
or shell runtime tests:

```bash
docker build --tag infralink-ops-gitea-gitleaks:test .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-gitea-gitleaks:test /tests/receive-gate.sh

docker build --tag infralink-ops-runtime:test -f Dockerfile.ops .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-runtime:test /tests/runtime-image.sh
```

## Commit And PR Scope

Use Conventional Commits. Prefer small commits that stand alone:

- `docs:` for contributor, operator, and security guidance.
- `test:` for boundary or validation coverage.
- `feat:` for new public runtime behavior.
- `fix:` for behavior corrections.
- `chore:` for mechanical maintenance.

Keep generated or mechanical changes separate from authored prose. Draft PRs
should list the intent of each commit and the validation commands run.

## Public Data Policy

Public examples must use sanitized data only: `example.com`, RFC 5737 IPv4
addresses, generated UUIDs, generic aliases, and fake secret references. Do not
copy private hostnames, live endpoints, secret identifiers, project names,
deployment paths, private CI values, or operational runbook facts into public
files.

When a private reference implementation suggests a useful general behavior,
write a sanitized public requirement first and implement from that requirement.
Ambiguous material should be raised for human judgment.
