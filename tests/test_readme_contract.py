from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_registry_lifecycle_handoff() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for token in [
        "## Registry Lifecycle Handoff",
        "The registry revision is the only desired-state selector.",
        "controller-configured registry checkout",
        "render",
        "materialize_config_tree",
        "infralink-controller-config-consumers validate|activate",
        "Live-service",
        "proof belongs to the environment controller",
    ]:
        assert token in text


def test_readme_rejects_legacy_host_specific_lifecycle_shortcuts() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "/opt/infra/scripts/self-deploy.sh" not in text
    assert "/opt/infra/registry" not in text
