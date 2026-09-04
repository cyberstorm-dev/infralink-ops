"""Bootstrap the fixed controller-owned host interface from explicit inputs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infralink_ops import controller_host_interface as host_interface
from infralink_ops.registry_checkout import fetch_configured_registry
from infralink_ops.registry_transport_trust import (
    RegistryTransportTrustError,
    materialize_registry_transport_trust,
)

SCHEMA_VERSION = "infralink.ops.controller-bootstrap/v1"
REGISTRY_ROOT = "/var/lib/infralink/registry"


class ControllerBootstrapError(ValueError):
    """Bootstrap inputs cannot safely establish the one controller runtime."""


class EnvelopeParser(argparse.ArgumentParser):
    """Keep invalid CLI usage inside the machine-readable response."""

    def error(self, message: str) -> None:
        raise ControllerBootstrapError("usage_error")


@dataclass(frozen=True)
class BootstrapConfiguration:
    """Explicit portable inputs required to initialize a host controller."""

    host_uuid: str
    bws_access_token: str
    deploy_key_secret_id: str
    registry_remote: str
    registry_ref: str
    registry_known_hosts: str


def _host_root(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ControllerBootstrapError("host_root_invalid")
    return path.resolve()


def _configuration(environ: Mapping[str, str], *, host_root: Path) -> BootstrapConfiguration:
    required = {
        "INFRALINK_HOST_UUID": "host_uuid",
        "BWS_ACCESS_TOKEN": "bws_access_token",
        "INFRALINK_REGISTRY_DEPLOY_KEY_SECRET_ID": "deploy_key_secret_id",
        "INFRALINK_REGISTRY_REPO_URL": "registry_remote",
        "INFRALINK_REGISTRY_REF": "registry_ref",
    }
    values: dict[str, str] = {}
    for environment_key, field_name in required.items():
        value = environ.get(environment_key, "")
        if not value:
            raise ControllerBootstrapError("bootstrap_configuration_required")
        values[field_name] = value

    known_hosts = environ.get("INFRALINK_REGISTRY_KNOWN_HOSTS", "")
    existing_known_hosts = host_root / "etc/infralink/registry-known_hosts"
    if not known_hosts and existing_known_hosts.is_file():
        known_hosts = existing_known_hosts.read_text(encoding="utf-8")
    if not known_hosts.strip() or "\x00" in known_hosts:
        raise ControllerBootstrapError("registry_transport_trust_required")

    return BootstrapConfiguration(registry_known_hosts=known_hosts, **values)


def _payload(
    *, command: str | None, result: dict[str, Any] | None = None, error: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": error is None,
        "command": {"path": [command] if command else []},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = {"code": error}
    return payload


def _plan(host_root: Path, configuration: BootstrapConfiguration) -> dict[str, Any]:
    return {
        "host_uuid": configuration.host_uuid,
        "registry": {
            "remote": configuration.registry_remote,
            "ref": configuration.registry_ref,
            "root": REGISTRY_ROOT,
        },
        "writes": [
            "/etc/machine-uuid",
            "/etc/infralink/host.env",
            "/etc/infralink/registry-read",
            "/etc/infralink/registry-known_hosts",
            REGISTRY_ROOT,
            "/usr/local/bin/infralink",
            "/usr/libexec/infralink/runtime",
            "/etc/systemd/system/infralink-host-reconcile.service",
            "/etc/systemd/system/infralink-host-reconcile.timer",
        ],
    }


def _host_path(host_root: Path, path: str) -> Path:
    destination = host_root / path.removeprefix("/")
    current = host_root
    for part in destination.relative_to(host_root).parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ControllerBootstrapError("bootstrap_path_unsafe")
    return destination


def _write_atomically(destination: Path, *, content: str, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise ControllerBootstrapError("bootstrap_write_failed") from error


def _write_host_uuid(host_root: Path, host_uuid: str) -> None:
    destination = _host_path(host_root, "/etc/machine-uuid")
    if destination.exists() or destination.is_symlink():
        metadata = destination.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ControllerBootstrapError("host_identity_unsafe")
        existing = destination.read_text(encoding="utf-8").strip()
        if existing and existing != host_uuid:
            raise ControllerBootstrapError("host_identity_conflict")
    _write_atomically(destination, content=f"{host_uuid}\n", mode=0o644)


def _read_bws_secret(secret_id: str) -> str:
    completed = subprocess.run(
        ["bws", "secret", "get", secret_id, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise ControllerBootstrapError("registry_deploy_key_unavailable")
    try:
        value = json.loads(completed.stdout).get("value")
    except json.JSONDecodeError as error:
        raise ControllerBootstrapError("registry_deploy_key_unavailable") from error
    if not isinstance(value, str) or not value:
        raise ControllerBootstrapError("registry_deploy_key_unavailable")
    return value


def _host_environment(configuration: BootstrapConfiguration) -> str:
    values = {
        "INFRALINK_HOST_UUID": configuration.host_uuid,
        "BWS_ACCESS_TOKEN": configuration.bws_access_token,
        "INFRALINK_REGISTRY_DEPLOY_KEY_SECRET_ID": configuration.deploy_key_secret_id,
        "INFRALINK_REGISTRY_REPO_URL": configuration.registry_remote,
        "INFRALINK_REGISTRY_REF": configuration.registry_ref,
        "INFRALINK_REGISTRY_KNOWN_HOSTS_FILE": "/etc/infralink/registry-known_hosts",
        "GIT_SSH_COMMAND": (
            "ssh -i /etc/infralink/registry-read -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/infralink/registry-known_hosts"
        ),
    }
    return "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items())


def _assert_no_competing_apply_loop(host_root: Path) -> None:
    if _host_path(host_root, "/etc/cron.d/self-deploy").exists():
        raise ControllerBootstrapError("competing_apply_loop_detected")
    legacy_timer = "self-deploy-v2-reconcile.timer"
    legacy_service = "self-deploy-v2-reconcile.service"
    if (
        _systemd_unit_is_active(legacy_timer)
        or _systemd_unit_is_enabled(legacy_timer)
        or _systemd_unit_is_active(legacy_service)
    ):
        raise ControllerBootstrapError("competing_apply_loop_detected")


def _systemd_unit_is_active(unit: str) -> bool:
    completed = subprocess.run(
        [
            "nsenter",
            "--target",
            "1",
            "--mount",
            "--pid",
            "--",
            "systemctl",
            "is-active",
            "--quiet",
            unit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode in {3, 4}:
        return False
    raise ControllerBootstrapError("apply_loop_state_unavailable")


def _systemd_unit_is_enabled(unit: str) -> bool:
    completed = subprocess.run(
        [
            "nsenter",
            "--target",
            "1",
            "--mount",
            "--pid",
            "--",
            "systemctl",
            "is-enabled",
            "--quiet",
            unit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode in {1, 3, 4}:
        return False
    raise ControllerBootstrapError("apply_loop_state_unavailable")


def _initialize_registry(host_root: Path, configuration: BootstrapConfiguration) -> str:
    registry = _host_path(host_root, REGISTRY_ROOT)
    key = _host_path(host_root, "/etc/infralink/registry-read")
    known_hosts = _host_path(host_root, "/etc/infralink/registry-known_hosts")
    if registry.exists() and not (registry / ".git").is_dir():
        if any(registry.iterdir()):
            raise ControllerBootstrapError("registry_checkout_invalid")
        registry.rmdir()
    if not registry.exists():
        completed = subprocess.run(
            [
                "git",
                "-c",
                "submodule.recurse=false",
                "clone",
                "--no-recurse-submodules",
                configuration.registry_remote,
                str(registry),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_SSH_COMMAND": (
                    f"ssh -i {shlex.quote(str(key))} -o IdentitiesOnly=yes "
                    "-o StrictHostKeyChecking=yes "
                    f"-o UserKnownHostsFile={shlex.quote(str(known_hosts))}"
                ),
            },
        )
        if completed.returncode:
            raise ControllerBootstrapError("registry_checkout_unavailable")
    try:
        return fetch_configured_registry(
            registry,
            configured_remote=configuration.registry_remote,
            configured_ref=configuration.registry_ref,
            identity_file=key,
            known_hosts_file=known_hosts,
        ).revision
    except Exception as error:
        raise ControllerBootstrapError("registry_checkout_unavailable") from error


def _enable_reconcile_timer(host_root: Path) -> None:
    command = [
        "nsenter",
        "--target",
        "1",
        "--mount",
        "--pid",
        "--",
        "systemctl",
        "enable",
        "--now",
        "infralink-host-reconcile.timer",
    ]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ControllerBootstrapError("reconcile_timer_enable_failed") from error


def _apply(host_root: Path, configuration: BootstrapConfiguration) -> dict[str, Any]:
    _assert_no_competing_apply_loop(host_root)
    _write_host_uuid(host_root, configuration.host_uuid)
    key = _host_path(host_root, "/etc/infralink/registry-read")
    _write_atomically(
        key,
        content=f"{_read_bws_secret(configuration.deploy_key_secret_id)}\n",
        mode=0o600,
    )
    known_hosts = _host_path(host_root, "/etc/infralink/registry-known_hosts")
    try:
        materialize_registry_transport_trust(
            content=configuration.registry_known_hosts,
            destination=known_hosts,
        )
    except RegistryTransportTrustError as error:
        raise ControllerBootstrapError("registry_transport_trust_write_failed") from error
    host_environment = _host_path(host_root, "/etc/infralink/host.env")
    _write_atomically(host_environment, content=_host_environment(configuration), mode=0o600)
    revision = _initialize_registry(host_root, configuration)
    interface = host_interface.refresh(host_root)
    _enable_reconcile_timer(host_root)
    result = _plan(host_root, configuration)
    result["registry"]["revision"] = revision
    result["host_interface"] = interface
    return result


def main(
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], int]:
    """Plan or apply one portable controller bootstrap request."""

    parser = EnvelopeParser(prog="infralink-controller-bootstrap")
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--host-root", type=Path, required=True)
    try:
        arguments = parser.parse_args(argv)
        host_root = _host_root(arguments.host_root)
        configuration = _configuration(
            environ if environ is not None else os.environ,
            host_root=host_root,
        )
    except ControllerBootstrapError as error:
        return _payload(command=None, error=str(error)), 64

    result = _plan(host_root, configuration)
    if arguments.command == "plan":
        result_payload = _payload(command="plan", result=result)
        result_payload["command"]["args"] = {"host_root": str(host_root)}
        return result_payload, 0
    try:
        return _payload(command="apply", result=_apply(host_root, configuration)), 0
    except ControllerBootstrapError as error:
        return _payload(command="apply", error=str(error)), 78
    except OSError:
        return _payload(command="apply", error="bootstrap_apply_failed"), 78


def cli() -> int:
    """Write one controller-bootstrap YAML envelope."""

    payload, status = main()
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    return status


if __name__ == "__main__":
    raise SystemExit(cli())
