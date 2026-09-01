# Private Prometheus evidence producer implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the private Infralink controller produce one signed,
atomically-replaced `infralink.fleet-prometheus-evidence/v1` artifact from
Registry-declared observation targets, without adding any public CLI or MCP
surface.

**Architecture:** `infralink-ops` consumes the released Infralink contract
model to construct and sign the evidence document. A controller-only library
entry point receives the verified Registry declaration from the existing
reconcile path, resolves only the declaration's credential and signing-key
references, performs bounded observation queries, and writes the complete
artifact only after every declared target has a terminal result. The existing
controller reconcile scheduler invokes that library; this change adds no
console-script entry point, public command, or MCP operation.

**Tech stack:** Python 3.12, released `infralink` contract model, Ed25519 via a
directly declared Python dependency, BWS CLI, standard-library HTTPS client,
and the existing safe artifact-install primitive.

---

## Release gates

Do not start implementation until both dependencies are available. Neither is
an optional compatibility path.

1. **Infralink contract release:** Merge Infralink PR
   [`#307`](https://github.com/cyberstorm-dev/infralink/pull/307) and release a
   wheel containing commit
   `d99f8978d47bbcd849fbc7c38f5d60d5051313f2` or a later corrected successor. The
   wheel must export:
   - `infralink.fleet.FleetPrometheusEvidence`,
     `FleetPrometheusEvidenceSignature`, and `FleetPrometheusTarget`;
   - `infralink.fleet.prometheus_evidence.SCHEMA_VERSION`; and
   - `infralink/schemas/fleet/prometheus-evidence-v1.json` plus the canonical
     fixture semantics supplied by `FleetPrometheusEvidence.canonical_signed_bytes()`,
     `FleetPrometheusEvidence.verify_signature()`, and
     `FleetPrometheusEvidence.is_fresh_at()`.

   The current released dependency is `infralink 0.6.20`, which does not
   contain these names. Pin `pyproject.toml` to the first released version that
   does contain them. Do not use a Git URL, a branch, a source checkout, or a
   locally copied Pydantic model. The released contract requires targets as a
   map keyed by the exact canonical target ID, signed `max_age_seconds`, and
   RFC3339 UTC timestamps with whole-second `Z` precision. The Pydantic model
   is the semantic authority; the distributed JSON Schema is structural only.

2. **Registry declaration release:** Complete
   [`infra-registry #711`](https://gitea.i.cyberstorm.dev/relaxgg/infra-registry/issues/711)
   with one versioned declaration that provides only:
   - stable observable target IDs;
   - the controller-side Prometheus credential binding reference; and
   - the controller-side signing-key binding reference.

   The declaration must not contain a Prometheus URL, query text, raw
   matcher, credential value, signing-key value, or produced evidence. Its
   published parser or schema is the only acceptable source for the Ops
   declaration reader. `infralink-ops` must not create a competing YAML shape.

The contract release alone is insufficient: an ID has no safe query meaning
until Registry #711 publishes the target declaration and the controller's
fixed target projection that it selects.

The operator-configured public verifier mapping is reader configuration. The
producer does not load, validate, or expose public verifier keys. It receives
only the Registry-declared private signing-key binding, emits its stable
`signature.key_id`, and is validated against the released test vector in
controller tests.

## Current implementation boundary

The following current code is reusable after the release gates:

| Existing component | Reuse | Reason |
| --- | --- | --- |
| `src/infralink_ops/artifact_target_install.py` | Yes | `install_artifact_body()` provides no-follow, atomic, fsynced, mode/owner-controlled replacement of an explicit controller-owned file. |
| `src/infralink_ops/stable_regular_file.py` | Yes | Reader/integration tests can prove that an artifact is observed as one stable regular file. |
| `src/infralink_ops/bounded_process.py` | Conditional | Use only if the approved controller binding requires a local helper process. Do not spawn `curl`, shell, or arbitrary user arguments. |
| `src/infralink_ops/controller_metrics.py` | Freshness metric pattern only | It demonstrates atomic textfile output but does not set the private artifact metadata required here. |
| `src/infralink_ops/controller_render_secrets.py` | No | It enumerates render-secret projects and writes shell exports. The evidence producer needs a narrow binding resolver that never emits credential or key material. |
| `src/infralink_ops/canonical_json.py` | No | Its Unicode serialization differs from the released contract's `ensure_ascii=True` canonical payload. Always call `FleetPrometheusEvidence.canonical_signed_bytes()`. |

Use `FleetPrometheusEvidence.model_validate()` for all evidence semantics,
including calendar-valid timestamps, status/detail combinations, sample-window
ordering, and strict integer fields. JSON Schema validation may be used only as
an optional structural cross-check; it is not an implementation substitute.

There is no generic reconcile-hook registration in this repository today. The
producer must therefore be a private library invoked by the existing
controller reconcile implementation, not a new runnable registered in
`pyproject.toml`.

## Task 1: Adopt the released contract without a compatibility fork

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_fleet_prometheus_evidence_producer.py`

- [ ] **Step 1: Write the failing released-contract import test**

```python
from infralink.fleet import FleetPrometheusEvidence
from infralink.fleet.prometheus_evidence import SCHEMA_VERSION


def test_uses_the_released_v1_evidence_contract() -> None:
    assert SCHEMA_VERSION == "infralink.fleet-prometheus-evidence/v1"
    assert FleetPrometheusEvidence.__module__ == "infralink.fleet.prometheus_evidence"


def test_released_contract_includes_the_verifier_api() -> None:
    assert callable(FleetPrometheusEvidence.verify_signature)
    assert callable(FleetPrometheusEvidence.is_fresh_at)
```

- [ ] **Step 2: Run the focused test before changing the dependency**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py
```

Expected: import failure while the project still resolves `infralink 0.6.20`.

- [ ] **Step 3: Pin the first released contract version**

Replace the broad Infralink dependency with the first released version that
contains the exact APIs in the release gate. Do not add a direct source URL or
copy the model/schema into this repository.

- [ ] **Step 4: Verify the contract import and installed wheel**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py
python -m build
python -m venv /tmp/infralink-ops-evidence-wheel
/tmp/infralink-ops-evidence-wheel/bin/pip install dist/*.whl
/tmp/infralink-ops-evidence-wheel/bin/python -c 'from infralink.fleet import FleetPrometheusEvidence; print(FleetPrometheusEvidence.__name__)'
```

Expected: the test passes and the installed wheel imports the released model.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_fleet_prometheus_evidence_producer.py
git commit -m "build: require released fleet evidence contract"
```

## Task 2: Parse only the Registry declaration and resolve narrow bindings

**Files:**
- Create: `src/infralink_ops/fleet_prometheus_evidence.py`
- Modify: `tests/test_fleet_prometheus_evidence_producer.py`

- [ ] **Step 1: Write failing declaration-boundary tests**

The tests must create a committed Registry fixture using the schema released
by Registry #711. They must reject declarations containing any of the
following keys at every level: `url`, `query`, `query_text`, `matcher`,
`password`, `token`, `value`, and `evidence`.

```python
def test_rejects_registry_declarations_with_transport_or_secret_values(...) -> None:
    declaration = valid_registry_declaration()
    declaration["targets"]["controller-api"]["query"] = "up"

    with pytest.raises(FleetEvidenceProducerError, match="declaration_invalid"):
        load_declared_prometheus_evidence_configuration(...)
```

- [ ] **Step 2: Run the focused declaration test**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py -k declaration
```

Expected: failure before the Registry #711 declaration reader exists.

- [ ] **Step 3: Implement the declaration reader and narrow resolver**

The reader must verify the Registry checkout revision before reading the
Registry #711 declaration. It returns only the declared target IDs and opaque
binding references. The resolver may look up exactly the two referenced BWS
objects and must pass their values directly to the private query/signing
functions. It must not print them, return shell exports, enumerate a project,
or accept a binding reference through an argument or environment variable.

- [ ] **Step 4: Verify declaration and secret boundaries**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py -k 'declaration or binding'
```

Expected: valid Registry-only target IDs and references load; all transport,
query, and secret-value variants fail before BWS is invoked.

- [ ] **Step 5: Commit**

```bash
git add src/infralink_ops/fleet_prometheus_evidence.py tests/test_fleet_prometheus_evidence_producer.py
git commit -m "feat: resolve declared fleet evidence bindings"
```

## Task 3: Produce bounded signed evidence atomically

**Files:**
- Modify: `src/infralink_ops/fleet_prometheus_evidence.py`
- Modify: `tests/test_fleet_prometheus_evidence_producer.py`

- [ ] **Step 1: Write failing producer tests**

Cover all Registry-declared targets, not a selected subset:

```python
def test_refresh_signs_and_atomically_replaces_complete_evidence(...) -> None:
    result = refresh_fleet_prometheus_evidence(...)

    evidence = FleetPrometheusEvidence.model_validate_json(read_stable_regular_file(output))
    assert tuple(evidence.targets) == ("controller-api", "edge-prober")
    assert evidence.max_age_seconds == 600
    assert evidence.verify_signature(public_key) is True
    assert result.status == "success"


def test_refresh_keeps_previous_evidence_when_one_target_query_cannot_finish(...) -> None:
    output.write_bytes(prior_valid_artifact)

    result = refresh_fleet_prometheus_evidence(...)

    assert result.status == "failure"
    assert output.read_bytes() == prior_valid_artifact
```

Also test more than 256 declared targets, an out-of-range window or
`max_age_seconds`, an unknown target projection, query timeout, response-size
overflow, invalid JSON, non-numeric sample data, invalid signing key, invalid
signature, fractional or offset timestamps, and output replacement durability
uncertainty. Verify the released Ed25519 test vector from Infralink PR #307
before adding controller-specific signing tests. The producer result must use
bounded reason codes and never include URL, query, response, credential, or key
material.

- [ ] **Step 2: Run the focused producer tests**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py -k refresh
```

Expected: failure before the producer exists.

- [ ] **Step 3: Implement one fixed controller query projection**

Use the query projection released with Registry #711. Query construction is
controller code, not Registry text. Enforce the fixed total deadline,
per-request deadline, concurrency limit, response-byte limit, and
`window_seconds` bounds selected in that release. Use a fixed controller policy
constant for `max_age_seconds`; do not accept it through Registry, an argument,
or the environment. Format `generated_at` and every non-null `observed_at` as
whole-second UTC strings using `YYYY-MM-DDTHH:MM:SSZ`. Map every terminal result
to the released `FleetPrometheusTarget` status/detail-code pair, and construct
the target map with each canonical target ID as its only identity. Do not add a
redundant nested `id` field. Construct one `FleetPrometheusEvidence`, call
`canonical_signed_bytes()`, sign those exact bytes with Ed25519, and replace
the configured controller-owned artifact by calling
`install_artifact_body(..., mode=0o640, ...)` only after the complete artifact
validates through `FleetPrometheusEvidence.model_validate()`.

The producer must retain the previous valid artifact on a failed refresh. It
must not write partial results or placeholder artifacts.

- [ ] **Step 4: Verify signing, atomicity, and output redaction**

Run:

```bash
python -m pytest -q tests/test_fleet_prometheus_evidence_producer.py -k 'refresh or redaction'
python -m ruff check src tests
python -m ruff format --check src tests
```

Expected: all result paths preserve a valid prior artifact on failure and
successful output validates with the released Infralink model.

- [ ] **Step 5: Commit**

```bash
git add src/infralink_ops/fleet_prometheus_evidence.py tests/test_fleet_prometheus_evidence_producer.py pyproject.toml
git commit -m "feat: produce signed fleet Prometheus evidence"
```

## Task 4: Expose producer freshness through the existing controller evidence path

**Files:**
- Modify: `src/infralink_ops/controller_metrics.py`
- Modify: `src/infralink_ops/controller_doctor.py`
- Modify: `src/infralink_ops/controller_reconcile_evidence.py`
- Modify: `tests/test_controller_metrics.py`
- Modify: `tests/test_controller_doctor.py`
- Modify: `tests/test_controller_reconcile_evidence.py`

- [ ] **Step 1: Write failing freshness tests**

```python
def test_doctor_reports_stale_fleet_evidence_producer(...) -> None:
    payload, status = main(...)

    assert status == 78
    assert payload["reason"] == "fleet_prometheus_evidence_producer_stale"


def test_producer_freshness_metric_exposes_no_target_or_transport_details(...) -> None:
    text = render_fleet_prometheus_evidence_freshness(...)

    assert "infralink_controller_fleet_prometheus_evidence_fresh" in text
    assert "http" not in text
    assert "controller-api" not in text


def test_producer_freshness_uses_exact_contract_clock_skew(...) -> None:
    evidence = read_valid_evidence(...)

    assert evidence.is_fresh_at(generated_at - timedelta(seconds=60)) is True
    assert evidence.is_fresh_at(generated_at - timedelta(seconds=61)) is False
    assert evidence.is_fresh_at(generated_at + timedelta(seconds=960)) is True
    assert evidence.is_fresh_at(generated_at + timedelta(seconds=961)) is False
```

- [ ] **Step 2: Run the focused freshness tests**

Run:

```bash
python -m pytest -q tests/test_controller_metrics.py tests/test_controller_doctor.py -k fleet_prometheus
```

Expected: failure before the producer freshness evidence exists.

- [ ] **Step 3: Add producer freshness only**

Publish a controller-local freshness result and metric that state whether the
last complete artifact refresh is fresh according to
`FleetPrometheusEvidence.is_fresh_at(now)`. This applies the signed
`max_age_seconds` and the contract's exact 60-second clock skew before and
after the freshness interval. The metric derives freshness from the artifact's
whole-second `generated_at` and does not introduce a separate mutable timeout.
Do not publish target IDs, Prometheus endpoint information, query text, raw
samples, credentials, signing keys, or the signed artifact itself. Extend
doctor to report an explicit stale/missing producer reason without triggering a
refresh or repair.

- [ ] **Step 4: Verify the existing controller evidence contract**

Run:

```bash
python -m pytest -q tests/test_controller_metrics.py tests/test_controller_doctor.py tests/test_controller_reconcile_evidence.py
```

Expected: existing reconcile evidence remains intact and producer freshness is
separately observable.

- [ ] **Step 5: Commit**

```bash
git add src/infralink_ops/controller_metrics.py src/infralink_ops/controller_doctor.py src/infralink_ops/controller_reconcile_evidence.py tests/test_controller_metrics.py tests/test_controller_doctor.py tests/test_controller_reconcile_evidence.py
git commit -m "feat: expose fleet evidence producer freshness"
```

## Task 5: Establish the private reconcile call site before wiring it

**Files:**
- Modify: the controller-image entrypoint and its focused controller-image
  tests in the control-plane repository that owns `infralink-host reconcile`
- Modify: the corresponding Infralink Ops controller-image integration test

**Current blocker:** The installed host service invokes
`/usr/local/sbin/infralink-host reconcile`, and the host launcher passes the
literal `reconcile` command to the controller image. This repository's current
`Dockerfile.controller` has no `ENTRYPOINT` dispatcher and has only
`CMD ["infralink-ops", "--help"]`; it does not implement that reconcile command.
The only discovered reconcile script is the legacy
`infra-management/scripts/infralink-controller-reconcile`, which is outside
this repository and is a migration target. Do not wire this new producer into
that legacy script or add a second runnable.

- [ ] **Step 1: Write a failing controller-image dispatch test in the
  controller-image owner**

```python
def test_reconcile_dispatches_to_the_private_ops_library_without_a_console_script(...) -> None:
    completed = run_controller_image("reconcile", private_reconcile_fixture)

    assert completed.returncode == 0
    assert evidence_path.is_file()
    assert "infralink-controller-prometheus" not in installed_console_scripts()
```

- [ ] **Step 2: Run the focused dispatch test**

Expected: failure until the control-plane owner supplies one private reconcile
dispatcher. The dispatcher must be part of the existing controller image; it
must not be a new command, timer, public CLI, or MCP operation.

- [ ] **Step 3: Invoke the Task 3 library from the approved dispatcher**

Call the library only after verified Registry checkout and before controller
success evidence is finalized. A producer failure records separate freshness
failure and retains the previous artifact. It must not initiate a retry loop,
public command, direct repair, or an additional timer.

- [ ] **Step 4: Verify private integration and package surface**

Run the control-plane image test plus:

```bash
python -m pytest -q
python -m build
python -m ruff check src tests
python -m ruff format --check src tests
```

Expected: the controller produces evidence only from a verified Registry
declaration, and no additional public or private console script is registered.

- [ ] **Step 5: Commit in the controller-image owner**

Commit the controller-image dispatcher and its focused integration test with
the message `feat: refresh fleet evidence during controller reconcile`.

## Handoff acceptance matrix

The implementation is ready for the three-layer fixture only when all rows
hold for the same Registry revision:

| Case | Required result |
| --- | --- |
| Valid declaration and all samples | One validated, signed, complete artifact; freshness healthy. |
| Missing sample | Complete artifact with the declared target marked `absent`; no partial write. |
| Query/provider failure | Complete artifact only when every target has a released terminal status; previous valid artifact remains on refresh failure. |
| Invalid Registry declaration | No BWS lookup, network request, artifact write, or public surface change. |
| Stale producer | Doctor/freshness calls `is_fresh_at()` with the signed `max_age_seconds` and exact +/-60-second skew, reports a bounded stale reason, and does not trigger a reader refresh. |
| Invalid signing material | No artifact replacement; no key material appears in output or metrics. |
| Public Infralink invocation | Reads the later configured artifact only; it has no URL, credential, target-file, SSH, Docker, BWS, or repair input. |

After this matrix passes, hand off to the cross-repository fixture work. Do not
switch or delete `prometheus_qa.py` or `check_prom_freshness.py` until the
fixture proves Registry declaration, controller artifact, and
`infralink fleet validate --live` together.
