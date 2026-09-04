from pathlib import Path

import tomllib


def test_controller_image_is_a_public_ops_runtime() -> None:
    recipe = Path("Dockerfile.controller").read_text(encoding="utf-8")

    assert recipe.startswith("FROM python@sha256:")
    assert "COPY src ./src" in recipe
    assert "python -m pip install --disable-pip-version-check --no-cache-dir ." in recipe
    assert "command -v infralink-controller-" not in recipe
    assert 'ENTRYPOINT ["python", "-m", "infralink_ops.controller_runtime"]' in recipe
    for private_tree in ("COPY ansible", "COPY lib", "COPY monitoring", "COPY scripts"):
        assert private_tree not in recipe


def test_controller_image_installs_iproute2_for_firewall_network_inventory() -> None:
    recipe = Path("Dockerfile.controller").read_text(encoding="utf-8")

    assert "iproute2" in recipe


def test_controller_image_requires_the_current_typed_infralink_surface() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    recipe = Path("Dockerfile.controller").read_text(encoding="utf-8")

    assert "infralink>=0.6.25,<0.7" in project["project"]["dependencies"]
    assert "agent-surface>=0.2.3,<0.3" in project["build-system"]["requires"]
    assert "infralink runtime is older than 0.6.25" in recipe


def test_ops_has_no_public_console_entrypoints() -> None:
    scripts = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "[project.scripts]" not in scripts
    assert "infralink-controller-" not in scripts
    assert not Path("Dockerfile.ops").exists()


def test_controller_reconcile_evidence_is_private_to_runtime() -> None:
    scripts = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "infralink-controller-metrics" not in scripts
    assert "infralink-controller-reconcile-evidence" not in scripts
    assert "infralink-controller-registry-checkout" not in scripts
