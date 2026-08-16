"""Agent-oriented command line surface for Infralink operational runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml

from infralink_ops.observation import project_registry_observation


@click.command()
@click.option("--registry-root", type=click.Path(path_type=Path), required=True)
@click.option("--observation-directory", required=True)
@click.option("--expected-revision", required=True)
def run(registry_root: Path, observation_directory: str, expected_revision: str) -> None:
    """Project typed registry observation declarations as YAML."""

    result = project_registry_observation(
        registry_root,
        observation_directory=observation_directory,
        expected_revision=expected_revision,
        as_of=datetime.now(timezone.utc),
    )
    payload: dict[str, Any] = {
        "schema_version": "infralink.ops.cli/v1",
        "ok": True,
        "result": result.to_dict(),
        "next_actions": [],
    }
    click.echo(yaml.safe_dump(payload, sort_keys=False))
