from __future__ import annotations

import json

import pytest

from infralink_ops import compose_state_protocol as protocol

CONTAINER_ID = "a" * 64
LOCAL_IMAGE_ID = "sha256:" + ("b" * 64)
REPO_DIGEST = "ghcr.io/example/app@sha256:" + ("c" * 64)


def _record(*atoms: object) -> bytes:
    return ("\x1f".join(json.dumps(atom, separators=(",", ":")) for atom in atoms) + "\n").encode()


def test_builds_exact_bounded_docker_commands() -> None:
    assert protocol.version_command() == (
        "/usr/bin/docker",
        "--host",
        "unix:///var/run/docker.sock",
        "version",
        "--format",
        protocol.VERSION_TEMPLATE,
    )
    assert protocol.list_command("services")[-4:] == (
        "--filter",
        "label=com.docker.compose.project=services",
        "--format",
        protocol.LIST_TEMPLATE,
    )
    assert protocol.container_inspect_command((CONTAINER_ID,))[-1] == CONTAINER_ID
    assert protocol.image_inspect_command((LOCAL_IMAGE_ID,))[-1] == LOCAL_IMAGE_ID


def test_templates_use_json_atoms_and_an_unambiguous_separator() -> None:
    for template in (
        protocol.VERSION_TEMPLATE,
        protocol.LIST_TEMPLATE,
        protocol.CONTAINER_TEMPLATE,
        protocol.IMAGE_TEMPLATE,
    ):
        assert '{{printf "%c" 31}}' in template
        assert "\t" not in template


def test_parses_typed_records_from_the_docker_wire_format() -> None:
    version = protocol.parse_version_output(_record("28.3.2", "28.3.2", "1.51", "1.51"))
    listed = protocol.parse_listing_output(
        _record(CONTAINER_ID, "services", "app", "1", "False"), project_name="services"
    )
    container = protocol.parse_container_output(
        _record(
            CONTAINER_ID,
            LOCAL_IMAGE_ID,
            "ghcr.io/example/app:v1",
            "running",
            True,
            False,
            False,
            False,
            "healthy",
            0,
            "services",
            "app",
            "1",
            "False",
        ),
        requested_ids=(CONTAINER_ID,),
    )
    image = protocol.parse_image_output(
        _record(LOCAL_IMAGE_ID, [REPO_DIGEST]), requested_ids=(LOCAL_IMAGE_ID,)
    )

    assert version.server_api_version == "1.51"
    assert listed[0].service == "app"
    assert container[0].configured_image.repository == "ghcr.io/example/app"
    assert container[0].health_status == "healthy"
    assert image[0].repo_digests == (REPO_DIGEST,)


@pytest.mark.parametrize(
    ("call", "reason"),
    (
        (lambda: protocol.list_command("Bad"), "listing_output"),
        (
            lambda: protocol.container_inspect_command((CONTAINER_ID, CONTAINER_ID)),
            "container_output",
        ),
        (lambda: protocol.parse_version_output(b'"28.3.2"\n'), "version_output"),
        (
            lambda: protocol.parse_container_output(
                _record(
                    CONTAINER_ID,
                    LOCAL_IMAGE_ID,
                    "ghcr.io/example/app:v1",
                    "running",
                    True,
                    True,
                    False,
                    False,
                    None,
                    0,
                    "services",
                    "app",
                    "1",
                    "False",
                ),
                requested_ids=(CONTAINER_ID,),
            ),
            "lifecycle",
        ),
    ),
)
def test_protocol_rejects_untrusted_docker_input(call: object, reason: str) -> None:
    with pytest.raises(protocol.ComposeStateProtocolError) as failure:
        call()  # type: ignore[operator]
    assert failure.value.reason == reason


@pytest.mark.parametrize(
    "body",
    (
        b' "28.3.2"\x1f"28.3.2"\x1f"1.51"\x1f"1.51"\n',
        b'"28.3.2"\x1f"28.3.2"\x1f"1.51"\x1f"1.51"\r\n',
        b'"28.3.2"\x1f"28.3.2"\x1f"1.51"\x1f"1.51"\n\n',
    ),
)
def test_protocol_rejects_noncanonical_wire_atoms(body: bytes) -> None:
    with pytest.raises(protocol.ComposeStateProtocolError) as failure:
        protocol.parse_version_output(body)
    assert failure.value.reason == "version_output"


def test_configured_image_rejects_invalid_unicode() -> None:
    with pytest.raises(ValueError):
        protocol.parse_configured_image("ghcr.io/example/\ud800")


def test_selects_one_distribution_identity_for_a_configured_image() -> None:
    selected = protocol.select_distribution_identity(
        protocol.parse_configured_image("ghcr.io/example/app:v1"),
        ("registry.example.invalid/alias@sha256:" + ("d" * 64), REPO_DIGEST),
    )
    assert selected.image == REPO_DIGEST
    assert selected.digest == "sha256:" + ("c" * 64)
