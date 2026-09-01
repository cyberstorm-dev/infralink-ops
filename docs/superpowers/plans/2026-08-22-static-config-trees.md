# Static Configuration Trees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materialize a registry-declared static configuration directory atomically into a controller-owned directory beneath `/opt/services/config`.

**Architecture:** `infralink-ops` gains a small pure-Python runtime module that validates a registry checkout at its active Git revision, parses one typed tree declaration, preflights both complete trees, and synchronizes only the declared target. It returns changed relative paths for the existing controller consumer activation path; the registry remains the sole desired-state authority and no content hash or persistent plan is introduced.

**Tech Stack:** Python 3.10+, pathlib, shutil, os.replace, PyYAML, pytest.

---

## File Structure

- Create `src/infralink_ops/config_trees.py`: declaration parsing, verified registry/source traversal, preflight, atomic tree synchronization, and structured change result.
- Create `tests/test_config_trees.py`: isolated Git-checkout fixtures and contract coverage for invalid source/target trees and complete desired-tree synchronization.
- Modify `src/infralink_ops/__init__.py`: export the runtime entrypoint and result type.
- Modify `README.md`: document that this package owns direct, revision-verified registry projections.
- Modify `infra-management` only in a follow-up PR: delegate typed `artifact-tree` execution to this package and pass returned paths to the existing consumer executor. Do not duplicate tree traversal there.

### Task 1: Define the Verified Static-Tree Projection

**Files:**
- Create: `src/infralink_ops/config_trees.py`
- Test: `tests/test_config_trees.py`

- [ ] **Step 1: Write failing source and target validation tests**

```python
def test_rejects_traversal_and_symlink_sources_before_target_mutation(tmp_path):
    root, revision = registry_checkout(tmp_path)
    declaration = {"source": "../outside", "target": "/opt/services/config/irc/static"}

    with pytest.raises(ValueError, match="source must be a directory below registry root"):
        materialize_config_tree(root, expected_revision=revision, declaration=declaration, services_root=tmp_path / "services")

    assert not (tmp_path / "services" / "config" / "irc" / "static").exists()
```

- [ ] **Step 2: Run the validation test and verify it fails**

Run: `python -m pytest -q tests/test_config_trees.py::test_rejects_traversal_and_symlink_sources_before_target_mutation`

Expected: FAIL because `materialize_config_tree` does not exist.

- [ ] **Step 3: Implement verified declaration and tree preflight**

```python
@dataclass(frozen=True)
class ConfigTreeResult:
    changed_paths: tuple[str, ...]


def materialize_config_tree(
    registry_root: Path,
    *,
    expected_revision: str,
    declaration: Mapping[str, Any],
    services_root: Path = Path("/opt/services"),
) -> ConfigTreeResult:
    root = verified_registry_root(registry_root, expected_revision)
    source = declared_source_directory(root, declaration["source"])
    target = declared_target_directory(services_root, declaration["target"])
    preflight_tree(source, target)
    return synchronize_tree(source, target, metadata=declaration)
```

`verified_registry_root` must require the checkout top-level and exact `HEAD == expected_revision`; source and target helpers must reject absolute source paths, `..`, symlinks, special files, and targets outside `<services_root>/config`.

- [ ] **Step 4: Run the validation test and verify it passes**

Run: `python -m pytest -q tests/test_config_trees.py::test_rejects_traversal_and_symlink_sources_before_target_mutation`

Expected: PASS.

- [ ] **Step 5: Commit the validation contract**

```bash
git add src/infralink_ops/config_trees.py tests/test_config_trees.py
git commit -m "feat: validate registry-declared config trees"
```

### Task 2: Synchronize Complete Controller-Owned Trees

**Files:**
- Modify: `src/infralink_ops/config_trees.py`
- Modify: `tests/test_config_trees.py`

- [ ] **Step 1: Write failing complete-tree tests**

```python
def test_sync_updates_nested_files_removes_stale_entries_and_is_idempotent(tmp_path):
    root, revision = registry_checkout_with_tree(tmp_path, {"a/base.conf": "new\\n", "b/extra.conf": "ok\\n"})
    target = tmp_path / "services" / "config" / "irc" / "static"
    target.mkdir(parents=True)
    (target / "a").mkdir()
    (target / "a" / "base.conf").write_text("old\\n")
    (target / "stale.conf").write_text("remove\\n")

    first = materialize_config_tree(root, expected_revision=revision, declaration=DECLARATION, services_root=tmp_path / "services")
    second = materialize_config_tree(root, expected_revision=revision, declaration=DECLARATION, services_root=tmp_path / "services")

    assert (target / "a" / "base.conf").read_text() == "new\\n"
    assert not (target / "stale.conf").exists()
    assert first.changed_paths == ("irc/static/a/base.conf", "irc/static/b/extra.conf", "irc/static/stale.conf")
    assert second.changed_paths == ()
```

- [ ] **Step 2: Run the synchronization test and verify it fails**

Run: `python -m pytest -q tests/test_config_trees.py::test_sync_updates_nested_files_removes_stale_entries_and_is_idempotent`

Expected: FAIL because the preflight-only implementation does not synchronize files.

- [ ] **Step 3: Implement atomic synchronization and stale cleanup**

```python
def write_file_atomically(source: Path, target: Path, mode: int, uid: int, gid: int) -> bool:
    content = source.read_bytes()
    if target.is_file() and target.read_bytes() == content and stat_matches(target, mode, uid, gid):
        return False
    temporary = target.with_name(f".{target.name}.infralink-tmp")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.chown(temporary, uid, gid)
    os.replace(temporary, target)
    return True
```

Create destination directories with declared mode/ownership before writes. Compare the source file set to the target file set, reject target symlinks/type conflicts before any write, then remove only stale files and empty directories below the declared target. Return paths relative to `/opt/services/config` in stable lexical order.

- [ ] **Step 4: Run the synchronization tests and verify they pass**

Run: `python -m pytest -q tests/test_config_trees.py`

Expected: PASS, including nested updates, stale cleanup, idempotency, atomic replacement, and type-conflict rejection.

- [ ] **Step 5: Commit the runtime projection**

```bash
git add src/infralink_ops/config_trees.py tests/test_config_trees.py
git commit -m "feat: materialize controller-owned config trees"
```

### Task 3: Expose the Runtime Contract and Prepare Controller Delegation

**Files:**
- Modify: `src/infralink_ops/__init__.py`
- Modify: `README.md`
- Test: `tests/test_config_trees.py`

- [ ] **Step 1: Write a public API test**

```python
from infralink_ops import ConfigTreeResult, materialize_config_tree


def test_public_config_tree_api_is_importable():
    assert ConfigTreeResult.__name__ == "ConfigTreeResult"
    assert callable(materialize_config_tree)
```

- [ ] **Step 2: Run the public API test and verify it fails**

Run: `python -m pytest -q tests/test_config_trees.py::test_public_config_tree_api_is_importable`

Expected: FAIL because the symbols are not exported.

- [ ] **Step 3: Export and document the narrow contract**

```python
from infralink_ops.config_trees import ConfigTreeResult, materialize_config_tree

__all__ = ["ConfigTreeResult", "materialize_config_tree"]
```

Document that consumers must provide the configured registry checkout and exact revision; the package does not fetch Git, store plans, choose revisions, or invoke services.

- [ ] **Step 4: Run quality checks and verify they pass**

Run: `python -m pytest -q && python -m ruff check src tests`

Expected: PASS.

- [ ] **Step 5: Commit and open the library PR against issue #17**

```bash
git add src/infralink_ops/__init__.py README.md tests/test_config_trees.py
git commit -m "docs: expose config tree runtime contract"
git push -u origin feat/config-tree-runtime
gh pr create --repo cyberstorm-dev/infralink-ops --base main --head feat/config-tree-runtime --title "feat: materialize registry-declared config trees" --body "Implements the reusable runtime half of #17; infra-management#536 will delegate its controller executor to this package."
```

### Task 4: Delegate the Existing Controller Executor

**Files:**
- Modify: the legacy artifact materialization script in `infra-management/scripts/`
- Modify: `infra-management/scripts/tests/test_infralink_controller_artifacts.py`
- Test: `infra-management/scripts/tests/test_infralink_controller_reconcile.py`

- [ ] **Step 1: Write the failing controller integration test**

```python
def test_controller_delegates_declared_artifact_tree_and_activates_consumer(...):
    result = run_controller_for_tree_declaration(...)
    assert result["changed_config_paths"] == ["irc-stacks/platform/inspircd/static/modules.conf"]
    assert consumer_log.read_text().contains("inspircd")
```

- [ ] **Step 2: Run the controller integration test and verify it fails**

Run: `python -m pytest -q scripts/tests/test_infralink_controller_artifacts.py -k artifact_tree`

Expected: FAIL because infra-management does not yet import `infralink_ops.materialize_config_tree`.

- [ ] **Step 3: Delegate, do not reimplement**

```python
from infralink_ops import materialize_config_tree

result = materialize_config_tree(
    registry_root,
    expected_revision=registry_revision,
    declaration=artifact_tree,
    services_root=services_dir,
)
changed_paths.extend(result.changed_paths)
```

The executor must only adapt its existing typed declaration to the library call and pass `changed_paths` to its existing config-consumer mechanism. It must not calculate traversal, file digests, stale paths, or ownership itself.

- [ ] **Step 4: Run integration and contract checks**

Run: `python -m pytest -q scripts/tests/test_infralink_controller_artifacts.py scripts/tests/test_infralink_controller_reconcile.py && python -m ruff check scripts`

Expected: PASS.

- [ ] **Step 5: Commit and open the infra-management PR linked to #536**

```bash
git add scripts/ tests/
git commit -m "feat: delegate static config trees to infralink ops"
git push -u origin feat/config-tree-runtime-delegation
gh pr create --repo relax-dot-gg/infra-management --base main --head feat/config-tree-runtime-delegation --title "feat: delegate static config trees to infralink ops" --body "Implements infra-management#536 using the generic infralink-ops runtime from cyberstorm-dev/infralink-ops#17."
```

## Self-Review

- **Spec coverage:** Tasks 1 and 2 cover checkout authority, traversal/symlink/type rejection, atomic writes, metadata, stale cleanup, and idempotency. Task 4 covers change reporting and consumer activation. The first registry consumer is intentionally a separate registry PR after both runtime PRs merge.
- **Boundaries:** No task adds a Git fetch, ref selector, digest field, persisted plan, or direct service restart to `infralink-ops`; the controller remains the only deployment invoker.
- **Placeholder scan:** No TODO placeholders. All implementation steps name files, symbols, commands, and expected outcomes.
- **Type consistency:** `materialize_config_tree` and `ConfigTreeResult.changed_paths` are the only public runtime symbols consumed by the controller integration.
