"""Operational runtime for direct Infralink registry projections."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ArtifactTargetDurabilityUncertainError": "artifact_target_install",
    "ArtifactTargetError": "artifact_target_install",
    "ArtifactTargetResult": "artifact_target_install",
    "BoundedProcessFailure": "bounded_process",
    "BoundedProcessResult": "bounded_process",
    "ConfigTreeResult": "config_trees",
    "DeclaredFileDestinationError": "declared_file_destination",
    "EgressSnatError": "egress_snat",
    "EgressSnatRule": "egress_snat",
    "EgressSnatSnapshot": "egress_snat",
    "StableRegularFileError": "stable_regular_file",
    "capture_egress_snat": "egress_snat",
    "classify_declared_file_destination": "declared_file_destination",
    "install_artifact_body": "artifact_target_install",
    "load_registry_dashboards": "dashboards",
    "materialize_config_tree": "config_trees",
    "preflight_config_trees": "config_trees",
    "project_registry_observation": "observation",
    "read_stable_regular_file": "stable_regular_file",
    "reconcile_egress_snat": "egress_snat",
    "repair_empty_declared_file_destination": "declared_file_destination",
    "restore_egress_snat": "egress_snat",
    "run_bounded_process": "bounded_process",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load optional runtime helpers only when their public compatibility export is used."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(f"infralink_ops.{module_name}"), name)
