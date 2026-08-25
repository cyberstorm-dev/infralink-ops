# Declared Template Sources

**Goal:** Replace private renderer callbacks and product-specific source detection with one
registry-declared, revision-verified template-source contract.

**Authority:** The caller supplies the already selected registry checkout and expected revision.
This capability never fetches, selects, or persists a registry revision.

## Design

A host manifest may declare `template_sources` as a list of `{id, source}` mappings. `id` is
an identifier and `source` is a registry-relative directory. Templates include source files via
the constrained `sources/<id>/...` namespace. The renderer maps only declared aliases, rejects
path traversal and symlinks, and verifies the selected registry revision. A source that crosses a
submodule is valid only when its checkout is present, clean, and at the parent Gitlink revision.

The same source resolver is used by rendering and dependency discovery. Dependency output remains
registry-relative, so release validation can identify every declared source without private
product parsing.

## Steps

1. Add strict source declaration and resolved-source helpers with tests for literal and Jinja
   source templates plus invalid aliases, traversal, symlinks, and submodule state.
2. Build the Jinja loader from host templates plus declared source namespaces and remove private
   renderer extension callbacks.
3. Update dependency discovery to use the same loader and prove it reports declared source paths.
4. Release Ops. Migrate registry RelayOS declarations and remove management callbacks only after
   an end-to-end registry-change-to-rendered-artifact acceptance test proves equivalence.
