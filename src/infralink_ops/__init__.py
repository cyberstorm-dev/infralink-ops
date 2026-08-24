"""Operational runtime for direct Infralink registry projections."""

from infralink_ops.artifact_target_install import (
    ArtifactTargetDurabilityUncertainError,
    ArtifactTargetError,
    ArtifactTargetResult,
    install_artifact_body,
)
from infralink_ops.config_trees import (
    ConfigTreeResult,
    materialize_config_tree,
    preflight_config_trees,
)
from infralink_ops.dashboards import load_registry_dashboards
from infralink_ops.declared_file_destination import (
    DeclaredFileDestinationError,
    classify_declared_file_destination,
    repair_empty_declared_file_destination,
)
from infralink_ops.observation import project_registry_observation
from infralink_ops.stable_regular_file import StableRegularFileError, read_stable_regular_file

__all__ = [
    "ConfigTreeResult",
    "ArtifactTargetDurabilityUncertainError",
    "ArtifactTargetError",
    "ArtifactTargetResult",
    "DeclaredFileDestinationError",
    "classify_declared_file_destination",
    "load_registry_dashboards",
    "install_artifact_body",
    "materialize_config_tree",
    "preflight_config_trees",
    "project_registry_observation",
    "repair_empty_declared_file_destination",
    "StableRegularFileError",
    "read_stable_regular_file",
]
