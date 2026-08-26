# Independent Artifact Renderer Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authoring-only API for immutable artifact renderer sources.

**Architecture:** A small `artifact_renderer_source` module owns strict declaration parsing, clean-checkout verification, and deterministic declaration digesting. It is a library API only and cannot select controller code or apply host changes.

**Tech Stack:** Python, dataclasses, subprocess Git inspection, pytest.

---

### Task 1: Specify the source-pin contract

**Files:**
- Create: `tests/test_artifact_renderer_source.py`
- Create: `src/infralink_ops/artifact_renderer_source.py`

- [ ] Write tests for a valid exact source pin, invalid declarations, revision mismatch, dirty checkout rejection, and deterministic lock digest.
- [ ] Use these public calls in the test module:

```python
source = load_artifact_renderer_source(lock_path)
assert source.lock_digest == hashlib.sha256(lock_path.read_bytes()).hexdigest()
assert verify_artifact_renderer_checkout(source, checkout) == checkout.resolve()
```

```python
with pytest.raises(ArtifactRendererSourceError, match="revision"):
    verify_artifact_renderer_checkout(source, checkout)
```
- [ ] Run `python -m pytest -q tests/test_artifact_renderer_source.py` and observe failure because the module is absent.
- [ ] Implement only parsing, checkout validation, and digest derivation.
- [ ] Define `ArtifactRendererSourceError`, immutable `ArtifactRendererSource`,
  `load_artifact_renderer_source(lock_path)`, and
  `verify_artifact_renderer_checkout(source, checkout)` in the new module.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Verify repository quality gates

**Files:**
- Modify: `src/infralink_ops/artifact_renderer_source.py`
- Modify: `tests/test_artifact_renderer_source.py`

- [ ] Run `python -m ruff check src tests`.
- [ ] Run `python -m ruff format --check src tests`.
- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m build`.
- [ ] Commit the implementation and tests.
