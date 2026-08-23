from pathlib import Path


def test_controller_image_is_a_public_ops_runtime() -> None:
    recipe = Path("Dockerfile.controller").read_text(encoding="utf-8")

    for command in (
        "infralink-controller-registry-checkout",
        "infralink-controller-config-consumers",
        "infralink-controller-adapter",
        "infralink-controller-runtime-directories",
        "infralink-controller-host-interface",
        "infralink-controller-firewall",
        "infralink-controller-image-resolution",
        "infralink-controller-reference",
    ):
        assert f"command -v {command}" in recipe

    assert "COPY src ./src" in recipe
    for private_tree in ("COPY ansible", "COPY lib", "COPY monitoring", "COPY scripts"):
        assert private_tree not in recipe
