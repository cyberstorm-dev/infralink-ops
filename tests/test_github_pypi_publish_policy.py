"""Policy coverage for the Infralink Ops package publication workflow."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "publish-pypi.yml"


def load_workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_pypi_workflow_builds_only_an_existing_release_tag() -> None:
    workflow = load_workflow()
    build_steps = workflow["jobs"]["build"]["steps"]

    assert workflow[True] == {
        "workflow_dispatch": {
            "inputs": {
                "tag": {
                    "description": "Existing Infralink Ops release tag to publish to TestPyPI",
                    "required": True,
                    "type": "string",
                }
            }
        },
        "release": {"types": ["published"]},
    }
    assert build_steps[0]["with"]["ref"] == (
        "refs/tags/${{ github.event.release.tag_name || inputs.tag }}"
    )
    assert build_steps[3]["env"] == {
        "RELEASE_TAG": "${{ github.event.release.tag_name || inputs.tag }}"
    }


def test_pypi_workflow_uses_only_oidc_publishing_credentials() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["publish-testpypi"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert jobs["publish-pypi"]["if"] == "github.event_name == 'release'"

    for name, environment in (("publish-testpypi", "testpypi"), ("publish-pypi", "pypi")):
        job = jobs[name]
        assert job["permissions"] == {"id-token": "write"}
        assert job["environment"]["name"] == environment
        assert "from_secret" not in str(job)

    assert (
        jobs["publish-testpypi"]["steps"][-1]["with"]["repository-url"]
        == "https://test.pypi.org/legacy/"
    )


def test_testpypi_dispatch_can_resolve_testpypi_infralink_dependency() -> None:
    workflow = load_workflow()
    install_step = workflow["jobs"]["build"]["steps"][2]

    assert install_step["env"] == {
        "PIP_EXTRA_INDEX_URL": "${{ github.event_name == 'workflow_dispatch' && 'https://test.pypi.org/simple' || '' }}"
    }
