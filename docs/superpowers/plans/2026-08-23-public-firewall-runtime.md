# Public Firewall Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render and verify the portable Infralink firewall declaration with nftables without importing private environment policy.

**Architecture:** The public runtime receives an already selected `deployment.yml`, host UUID, and rendered Compose file. It validates the public `FirewallPolicy`, compares declared ingress with Compose publication, renders the owned `inet infralink_filter` table, and verifies the running table. The private controller retains registry selection, render sequencing, secret handling, and the existing egress-SNAT application until its dedicated extraction.

**Tech Stack:** Python 3.10+, PyYAML, public `infralink` v0.6.10, nftables CLI.

---

### Task 1: Add pure declaration and Compose rendering

**Files:**
- Create: `src/infralink_ops/firewall.py`
- Create: `tests/test_firewall.py`

- [ ] Write failing tests for a Tailnet ingress, a WAN ingress, no firewall declaration, a missing declared service, host-networking with Compose ports, and mismatched published port ownership.
- [ ] Implement `render_firewall_policy(firewall, compose)` and `load_firewall_policy(deployment)` with public `infralink.firewall.FirewallPolicy` only.
- [ ] Run `pytest -q tests/test_firewall.py` and format/lint checks.

### Task 2: Add bounded runtime verification

**Files:**
- Modify: `src/infralink_ops/firewall.py`
- Modify: `tests/test_firewall.py`

- [ ] Write failing tests for a matching `nft list table inet infralink_filter` result and a missing declared runtime rule.
- [ ] Implement `verify_firewall_policy` with an injected command runner and explicit `firewall_runtime_unavailable` / `firewall_runtime_drift` failures.
- [ ] Run the focused test suite.

### Task 3: Add controller runnable envelope

**Files:**
- Create: `src/infralink_ops/controller_firewall.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_controller_firewall.py`
- Modify: `README.md`

- [ ] Write failing tests for `render` and `verify` commands using explicit `--registry`, `--uuid`, and `--compose` inputs.
- [ ] Implement YAML envelope output in the controller firewall primitive.
- [ ] Add the public runtime boundary to README; do not document private host names, paths, BWS, Git, or controller selectors.
- [ ] Run full tests, Ruff, build, and `git diff --check`; open a PR against issue #53 for independent review.

### Follow-on boundary

`egress_snat` remains typed in the portable declaration but its controller-owned iptables realization is out of this nftables render/verify slice. Track and extract it separately before switching any SNAT-bearing private caller to this runtime.
