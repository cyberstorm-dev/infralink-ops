# Independent Artifact Renderer Source

## Goal

Provide an Ops library contract for a Registry-declared, immutable artifact-renderer source that is separate from controller source selection.

## Boundary

The contract parses a `repository` and exact Git `revision`, verifies a supplied clean checkout matches the declaration, and derives the SHA-256 lock value for the declaration bytes. It performs no fetch, writes no Registry data, and has no host or controller entry point.

## Consumers

Registry authoring and CI may use the contract to render artifacts and persist their lock references. Host reconciliation continues to use the existing management-source selection unchanged.

## Validation

Tests cover valid pins, malformed declarations, mismatched repository or revision, dirty checkouts, and deterministic lock digests.
