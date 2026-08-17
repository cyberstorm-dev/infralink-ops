# Infralink Ops

Operational support images consumed by registry declarations. The first image adds a
Gitleaks receive gate to Gitea. It uses Git's template directory for newly created
repositories and a registry-declared synchronizer for existing repositories.

## Development

Use Python 3.12 for the canonical local quality pass:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

The package declares Python `>=3.10`, and code should remain compatible with
Python 3.10+ unless a future issue explicitly raises the floor. The runtime
image currently uses Python 3.12.

Image checks mirror Woodpecker:

```bash
docker build --tag infralink-ops-gitea-gitleaks:test .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-gitea-gitleaks:test /tests/receive-gate.sh

docker build --tag infralink-ops-runtime:test -f Dockerfile.ops .
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/tests:ro" \
  infralink-ops-runtime:test /tests/runtime-image.sh
```

## Public Data Boundary

Public fixtures and docs must use sanitized examples only: `example.com`,
reserved documentation IP ranges, generated UUIDs, generic aliases, and fake
secret references. Do not copy private hostnames, live endpoints, secret names,
operational project identifiers, or private deployment facts into this repo.

See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and
[SECURITY.md](SECURITY.md).
