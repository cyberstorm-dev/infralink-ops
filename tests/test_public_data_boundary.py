import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_FIXTURE = ROOT / "tests" / "fixtures" / "private-host-boundary.md"
PUBLIC_STATIC_PATHS = (
    "Dockerfile",
    "Dockerfile.ops",
    "gitea-hooks/entrypoint",
    "gitea-hooks/install-receive-gate",
    "gitea-hooks/pre-receive-gitleaks",
    "tests/receive-gate.sh",
    "tests/runtime-image.sh",
)
PUBLIC_DOC_ROOTS = ("*.md", "docs/**/*.md")
PRIVATE_FIXTURES = {PRIVATE_FIXTURE.relative_to(ROOT).as_posix()}
FORBIDDEN_TERMS = (
    "private-host.internal",
    "live-tailnet",
    "secret-project-id",
    "production-token",
)
IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
HOST_FIELD = re.compile(r"(?im)^\s*(?:host|hostname|endpoint|url|source)\s*:\s*(?P<value>\S+)")
URL_AUTHORITY = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://(?P<authority>[^/\s`\"'<>]+)")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:token|password|secret|credential|api[_-]?key)\s*[:=]\s*[A-Za-z0-9_./+=-]{8,}"
)
DOCUMENTATION_RANGES = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)


def boundary_violations(text: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    for term in FORBIDDEN_TERMS:
        if term in lowered:
            violations.append(f"forbidden term: {term}")
    for match in IPV4.finditer(text):
        address = ipaddress.ip_address(match.group(0))
        if address.is_loopback:
            continue
        if not any(address in network for network in DOCUMENTATION_RANGES):
            violations.append(f"non-documentation IPv4 address: {address}")
    for match in URL_AUTHORITY.finditer(text):
        host = match.group("authority").rsplit("@", maxsplit=1)[-1].split(":", maxsplit=1)[0]
        if host not in {"example.com", "localhost"} and not host.endswith(".example.com"):
            violations.append(f"non-example URL host: {host}")
    for match in HOST_FIELD.finditer(text):
        value = match.group("value").strip("[]")
        url = URL_AUTHORITY.search(value)
        if url is not None:
            host = url.group("authority").rsplit("@", maxsplit=1)[-1].split(":", maxsplit=1)[0]
        else:
            host = value.rsplit("@", maxsplit=1)[-1].split(":", maxsplit=1)[0]
        if host and host not in {"example.com", "localhost"} and not host.endswith(".example.com"):
            violations.append(f"non-example host field: {host}")
    if SECRET_ASSIGNMENT.search(text):
        violations.append("secret-looking assignment")
    return violations


def public_files() -> tuple[Path, ...]:
    tracked = set(
        subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    candidates = {path for path in PUBLIC_STATIC_PATHS if (ROOT / path).exists()}
    for pattern in PUBLIC_DOC_ROOTS:
        candidates.update(path.relative_to(ROOT).as_posix() for path in ROOT.glob(pattern))

    return tuple(
        sorted(
            ROOT / path
            for path in tracked | candidates
            if path in PUBLIC_STATIC_PATHS
            or (path.endswith(".md") and path not in PRIVATE_FIXTURES)
        )
    )


def test_public_files_do_not_contain_private_operational_data() -> None:
    failures = {
        path.relative_to(ROOT).as_posix(): boundary_violations(path.read_text(encoding="utf-8"))
        for path in public_files()
        if boundary_violations(path.read_text(encoding="utf-8"))
    }

    assert failures == {}


def test_boundary_detector_rejects_private_host_fixture() -> None:
    assert boundary_violations(PRIVATE_FIXTURE.read_text(encoding="utf-8")) == [
        "forbidden term: private-host.internal",
        "non-example URL host: private-host.internal",
        "non-example host field: private-host.internal",
    ]
