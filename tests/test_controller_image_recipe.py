from pathlib import Path


def test_controller_image_is_a_public_ops_runtime() -> None:
    recipe = Path("Dockerfile.controller").read_text(encoding="utf-8")

    assert recipe.startswith("FROM python@sha256:")

    for command in (
        "infralink-controller-adapter",
        "infralink-controller-runtime-directories",
        "infralink-controller-host-interface",
        "infralink-controller-doctor",
        "infralink-controller-firewall",
        "infralink-controller-image-resolution",
        "infralink-controller-reference",
        "infralink-controller-template-dependencies",
    ):
        assert f"command -v {command}" in recipe

    assert "COPY src ./src" in recipe
    for private_tree in ("COPY ansible", "COPY lib", "COPY monitoring", "COPY scripts"):
        assert private_tree not in recipe


def test_ops_has_no_standalone_public_cli_entrypoint() -> None:
    scripts = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'infralink-ops = "infralink_ops.cli:run"' not in scripts
    assert not Path("Dockerfile.ops").exists()
    assert "infralink-controller-config-consumers" not in scripts


def test_controller_reconcile_evidence_is_private_to_runtime() -> None:
    scripts = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "infralink-controller-metrics" not in scripts
    assert "infralink-controller-reconcile-evidence" not in scripts
    assert "infralink-controller-registry-checkout" not in scripts
