import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
WOODPECKER = ROOT / ".woodpecker.yml"


def _github_anchor(text: str) -> str:
    anchor = text.strip().lower()
    anchor = re.sub(r"`", "", anchor)
    anchor = re.sub(r"<[^>]+>", "", anchor)
    anchor = re.sub(r"[^a-z0-9 _-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor)
    return anchor.strip("-")


def _anchors(markdown: str) -> set[str]:
    return {
        _github_anchor(match.group(2))
        for line in markdown.splitlines()
        if (match := re.match(r"^(#{1,6})\s+(.+?)\s*$", line))
    }


def _assert_local_markdown_links_resolve(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    anchors_by_doc: dict[Path, set[str]] = {path: _anchors(text)}

    for raw_target in LINK_RE.findall(text):
        if raw_target.startswith(("http://", "https://", "mailto:")):
            continue
        target, _, anchor = raw_target.partition("#")
        target_path = (path.parent / target).resolve() if target else path

        assert target_path.exists(), f"{path.relative_to(ROOT)} links missing {raw_target}"

        if anchor and target_path.suffix.lower() == ".md":
            anchors = anchors_by_doc.setdefault(
                target_path, _anchors(target_path.read_text(encoding="utf-8"))
            )
            assert anchor in anchors, (
                f"{path.relative_to(ROOT)} links missing anchor {raw_target}; "
                f"available anchors: {sorted(anchors)}"
            )


def test_readme_is_operator_entrypoint() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    for token in [
        "## BLUF",
        "## Reader Paths",
        "## How It Fits",
        "## Main CLIs",
        "## Authority Boundary",
        "## GHCR and BWS",
        "## Verify Changes",
        "## Triage A Stale Host",
        "[Controller runtime guide](docs/controller-runtime-guide.md)",
        "infralink-controller-registry-checkout fetch",
        "infralink-controller-config-consumers validate\\|activate",
        "infralink-controller-render-secrets",
        "infralink-controller-doctor",
        "infralink-host doctor\\|reconcile\\|bootstrap --apply",
        "https://github.com/orgs/cyberstorm-dev/packages",
        "ghcr.io/cyberstorm-dev/infralink-ops-controller",
        "/var/lib/infralink/registry",
        "/var/lib/infralink/reconcile-result.yml",
        "infralink-host-reconcile.timer",
        "/opt/services/config",
    ]:
        assert token in text


def test_installed_cli_quickstart_is_discoverable_and_safe() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "installed-cli-quickstart.md").read_text(encoding="utf-8")

    assert "[Installed CLI quickstart](docs/installed-cli-quickstart.md)" in readme
    for token in [
        "# Installed Infralink CLI Quickstart",
        "## BLUF",
        "## Before You Run Anything",
        "## Read-Only Host Evidence",
        "## Reconcile Only Through The Timer",
        "## Bootstrap Is An Explicit Host Change",
        "infralink-host doctor",
        "systemctl status infralink-host-reconcile.timer",
        "sudo systemctl start infralink-host-reconcile.service",
        "infralink-host bootstrap --apply",
        "machine-readable evidence",
        "Do not edit `/opt/services` directly",
    ]:
        assert token in quickstart


def test_woodpecker_exposes_pr_safe_docs_contract() -> None:
    text = WOODPECKER.read_text(encoding="utf-8")

    assert "docs-contract:" in text
    match = re.search(r"^  docs-contract:\n(?P<body>(?:    .+\n)+)", text, re.MULTILINE)
    assert match is not None
    docs_step = match.group("body")

    for token in [
        "image: python:3.12-slim-bookworm",
        "python -m pip install --disable-pip-version-check pytest",
        "python -m pytest -q tests/test_docs_contract.py",
    ]:
        assert token in docs_step

    for forbidden in [
        "from_secret:",
        "docker",
        "BWS",
        "ghcr.io",
        "python -m build",
    ]:
        assert forbidden not in docs_step


def test_docs_only_changes_do_not_publish_the_controller() -> None:
    text = WOODPECKER.read_text(encoding="utf-8")
    match = re.search(
        r"^  publish-infralink-ops-controller:\n(?P<body>(?:    .+\n)+)",
        text,
        re.MULTILINE,
    )

    assert match is not None
    assert (
        "exclude: [README.md, docs/**, tests/test_docs_contract.py, .woodpecker.yml]"
        in match.group("body")
    )


def test_controller_runtime_guide_documents_evidence_and_boundaries() -> None:
    text = (ROOT / "docs" / "controller-runtime-guide.md").read_text(encoding="utf-8")

    for token in [
        "# Infralink Ops controller runtime guide",
        "## BLUF",
        "## Runtime Lifecycle",
        "## Controller Primitives",
        "## BWS Contract",
        "## GHCR Contract",
        "## Triage A Stale Host",
        "infralink-controller-template-render",
        "materialize_config_tree",
        "infralink-controller-firewall render\\|verify",
        "infralink-controller-images retain-and-prune",
        "Use immutable `sha-<short-sha>` tags or digests for evidence",
        "Do not use `head` or `main` as acceptance evidence",
    ]:
        assert token in text


def test_docs_do_not_recommend_legacy_relayos_staging_controller_paths() -> None:
    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [ROOT / "README.md", ROOT / "docs" / "controller-runtime-guide.md"]
    )

    assert "/opt/infra/registry" not in corpus
    assert "self-deploy.sh" not in corpus


def test_markdown_links_resolve_for_operator_docs() -> None:
    for path in [
        ROOT / "README.md",
        ROOT / "docs" / "controller-runtime-guide.md",
        ROOT / "docs" / "installed-cli-quickstart.md",
    ]:
        _assert_local_markdown_links_resolve(path)
