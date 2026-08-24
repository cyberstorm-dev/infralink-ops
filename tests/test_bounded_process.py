from __future__ import annotations

import math
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from infralink_ops import bounded_process as process_module
from infralink_ops.bounded_process import (
    BoundedProcessFailure,
    BoundedProcessResult,
    run_bounded_process,
)

ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "XDG_CONFIG_HOME": "/nonexistent",
    "DOCKER_CONFIG": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
}


class StringSubclass(str):
    pass


class TupleSubclass(tuple):
    pass


class DictSubclass(dict):
    pass


def _invalid(
    field: str,
    values: tuple[object, ...],
) -> tuple[tuple[str, object], ...]:
    return tuple((field, value) for value in values)


def _kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cwd": "/",
        "environment": ENVIRONMENT.copy(),
        "deadline": time.monotonic() + 3.0,
        "stdout_limit": 300_000,
        "stderr_limit": 300_000,
        "aggregate_limit": 600_000,
        "term_grace_seconds": 0.5,
        "kill_grace_seconds": 0.5,
    }
    values.update(overrides)
    return values


def _run(argv: tuple[str, ...], **overrides: object) -> BoundedProcessResult:
    return run_bounded_process(argv, **_kwargs(**overrides))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        *_invalid(
            "argv",
            ((), [], TupleSubclass(("ok",)), ("",), (StringSubclass("ok"),), ("ok", 1)),
        ),
        *_invalid("cwd", (1, StringSubclass("/"), "relative", "//", "/tmp/../tmp")),
        *_invalid(
            "environment",
            (
                DictSubclass(),
                {"": "value"},
                {StringSubclass("KEY"): "value"},
                {"KEY": StringSubclass("value")},
                {"SECRET": 1},
            ),
        ),
        *_invalid("deadline", (True, 1, math.nan, math.inf, -math.inf)),
        *_invalid("stdout_limit", (True, 0)),
        *_invalid("stderr_limit", (1.0,)),
        *_invalid("aggregate_limit", (1, 700_000)),
        *_invalid("term_grace_seconds", (True, 1, -0.1, math.inf)),
        *_invalid("kill_grace_seconds", (math.nan,)),
    ),
)
def test_invalid_inputs_fail_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    calls = 0

    def forbidden_popen(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(process_module, "_popen", forbidden_popen)
    argv = value if field == "argv" else ("/usr/bin/true",)
    overrides = {} if field == "argv" else {field: value}
    category = (
        "output limits"
        if field.endswith("limit")
        else "cleanup grace"
        if field.endswith("seconds")
        else field
    )
    message = f"invalid process {category}"
    with pytest.raises(ValueError, match=f"^{message}$") as caught:
        _run(argv, **overrides)  # type: ignore[arg-type]
    assert calls == 0
    assert "SECRET" not in str(caught.value)
    assert "SECRET" not in repr(caught.value)


def test_expired_deadline_does_not_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_module, "_monotonic", lambda: 10.0)
    monkeypatch.setattr(
        process_module,
        "_popen",
        lambda *args, **kwargs: pytest.fail("spawned"),
    )
    with pytest.raises(BoundedProcessFailure) as caught:
        _run(("/secret/executable",), deadline=10.0)
    assert caught.value.reason == "timeout"
    assert str(caught.value) == "timeout"
    assert "secret" not in repr(caught.value)


def test_spawn_is_closed_and_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def missing(
        argv: list[str],
        **kwargs: object,
    ) -> subprocess.Popen[bytes]:
        calls.append((argv, kwargs))
        raise FileNotFoundError("secret executable")

    monkeypatch.setattr(process_module, "_popen", missing)
    with pytest.raises(BoundedProcessFailure) as caught:
        _run(("/secret/executable", "argument"))
    assert caught.value.reason == "spawn"
    assert calls == [
        (
            ["/secret/executable", "argument"],
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": "/",
                "env": ENVIRONMENT,
                "close_fds": True,
                "start_new_session": True,
                "bufsize": 0,
            },
        )
    ]
    assert repr(caught.value) == "BoundedProcessFailure(reason='spawn')"


def _write_executable(path: Path, body: str) -> str:
    path.write_text(f"#!{sys.executable}\n" + body, encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _fd_count() -> int:
    return len(tuple(Path("/proc/self/fd").iterdir()))


def _run_tracked_failure(
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    **overrides: object,
) -> BoundedProcessFailure:
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(process_module, "_popen", recording_popen)
    try:
        with pytest.raises(BoundedProcessFailure) as caught:
            _run(argv, **overrides)
        assert processes
        with pytest.raises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
    return caught.value


def test_real_success_has_exact_closed_context(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "probe",
        """
import os, sys
assert os.getcwd() == "/"
assert os.read(0, 1) == b""
assert os.getpid() == os.getpgrp() == os.getsid(0)
expected = {
    "PATH", "HOME", "XDG_CONFIG_HOME", "DOCKER_CONFIG", "LANG", "LC_ALL"
}
assert set(os.environ) == expected
sys.stdout.buffer.write(b"out")
sys.stderr.buffer.write(b"err")
raise SystemExit(7)
""",
    )
    result = _run((executable, "alpha"))
    assert result == BoundedProcessResult(7, b"out", b"err")


def test_concurrently_drains_stdout_and_stderr(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "pressure",
        """
import os, threading
barrier = threading.Barrier(3)
def write(fd, byte):
    barrier.wait()
    for _ in range(64):
        os.write(fd, byte * 4096)
threads = [
    threading.Thread(target=write, args=(1, b"o")),
    threading.Thread(target=write, args=(2, b"e")),
]
for thread in threads:
    thread.start()
barrier.wait()
for thread in threads:
    thread.join()
""",
    )
    result = _run((executable,))
    assert result.stdout == b"o" * 262_144
    assert result.stderr == b"e" * 262_144


@pytest.mark.parametrize(
    ("stdout", "stderr", "limits", "reason"),
    (
        (10, 0, (10, 10, 20), None),
        (11, 0, (10, 10, 20), "output"),
        (0, 10, (10, 10, 20), None),
        (0, 11, (10, 10, 20), "output"),
        (6, 4, (8, 8, 10), None),
        (6, 5, (8, 8, 10), "output"),
        (70_000, 0, (65_536, 65_536, 131_072), "output"),
    ),
)
def test_output_limits_are_exact(
    tmp_path: Path,
    stdout: int,
    stderr: int,
    limits: tuple[int, int, int],
    reason: str | None,
) -> None:
    executable = _write_executable(
        tmp_path / "output",
        f"import os\nos.write(1, b'o' * {stdout})\nos.write(2, b'e' * {stderr})\n",
    )
    kwargs = {
        "stdout_limit": limits[0],
        "stderr_limit": limits[1],
        "aggregate_limit": limits[2],
    }
    if reason is None:
        result = _run((executable,), **kwargs)
        assert (len(result.stdout), len(result.stderr)) == (stdout, stderr)
    else:
        with pytest.raises(BoundedProcessFailure) as caught:
            _run((executable,), **kwargs)
        assert caught.value.reason == reason


def test_single_read_crossing_cap_retains_one_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = SimpleNamespace(fileno=lambda: 123)
    selector = SimpleNamespace(
        select=lambda timeout: [(SimpleNamespace(fileobj=stream, data="stdout"), 1)]
    )
    monkeypatch.setattr(process_module, "_read", lambda fd, size: b"x" * 70_000)
    stdout = bytearray()
    reason = process_module._drain(
        selector,
        stdout,
        bytearray(),
        timeout=1.0,
        stdout_limit=10,
        stderr_limit=10,
        aggregate_limit=20,
    )
    assert reason == "output"
    assert stdout == b"x" * 11


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        ("import time\ntime.sleep(10)\n", "timeout"),
        (
            ("import os, time\nif os.fork() == 0:\n    time.sleep(10)\nraise SystemExit(0)\n"),
            "timeout",
        ),
        (
            (
                "import os, time\n"
                "read_fd, write_fd = os.pipe()\n"
                "if os.fork() == 0:\n"
                "    os.close(read_fd)\n"
                "    os.close(0); os.close(1); os.close(2)\n"
                "    os.write(write_fd, b'1'); os.close(write_fd)\n"
                "    time.sleep(10)\n"
                "os.close(write_fd)\n"
                "assert os.read(read_fd, 1) == b'1'\n"
                "os.close(read_fd)\n"
                "raise SystemExit(0)\n"
            ),
            "teardown",
        ),
    ),
)
def test_real_timeout_and_descendants_are_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    reason: str,
) -> None:
    executable = _write_executable(tmp_path / "child", body)
    descriptors = _fd_count()
    caught = _run_tracked_failure(
        monkeypatch,
        (executable,),
        deadline=time.monotonic() + 0.15,
        term_grace_seconds=0.1,
        kill_grace_seconds=0.2,
    )
    assert caught.reason == reason
    assert _fd_count() == descriptors


@pytest.mark.parametrize("fd", (1, 2), ids=("stdout", "stderr"))
def test_infinite_output_flood_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fd: int,
) -> None:
    executable = _write_executable(
        tmp_path / "flood",
        f"import os\nwhile True:\n    os.write({fd}, b'x' * 65536)\n",
    )
    caught = _run_tracked_failure(
        monkeypatch,
        (executable,),
        stdout_limit=8192,
        stderr_limit=8192,
        aggregate_limit=16_384,
    )
    assert caught.reason == "output"
    assert repr(caught) == "BoundedProcessFailure(reason='output')"


def test_term_refusal_escalates_to_group_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _write_executable(
        tmp_path / "refuse",
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n    time.sleep(1)\n",
    )
    delivered: list[int] = []
    real_killpg = os.killpg

    def recording_killpg(pid: int, sig: int) -> None:
        if sig:
            delivered.append(sig)
        real_killpg(pid, sig)

    monkeypatch.setattr(process_module, "_killpg", recording_killpg)
    caught = _run_tracked_failure(
        monkeypatch,
        (executable,),
        deadline=time.monotonic() + 0.15,
        term_grace_seconds=0.1,
        kill_grace_seconds=0.2,
    )
    assert caught.reason == "timeout"
    assert delivered[:2] == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("fault", ("selector", "read", "signal", "probe"))
def test_one_shot_boundary_faults_are_closed_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    executable = _write_executable(
        tmp_path / "sleep",
        "import time\ntime.sleep(10)\n",
    )
    if fault == "selector":
        real_factory = process_module._selector_factory

        class FaultySelector:
            def __init__(self) -> None:
                self._selector = real_factory()
                self._failed = False

            def __getattr__(self, name: str) -> object:
                return getattr(self._selector, name)

            def select(self, timeout: float) -> list[object]:
                if not self._failed:
                    self._failed = True
                    raise OSError("secret selector")
                return self._selector.select(timeout)

        monkeypatch.setattr(process_module, "_selector_factory", FaultySelector)
    elif fault == "read":
        real_read = os.read
        failed = False

        def faulty_read(fd: int, size: int) -> bytes:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("secret read")
            return real_read(fd, size)

        monkeypatch.setattr(process_module, "_read", faulty_read)
    else:
        real_killpg = os.killpg
        failed = False

        def faulty_killpg(pid: int, sig: int) -> None:
            nonlocal failed
            selected = sig == (signal.SIGTERM if fault == "signal" else 0)
            if selected and not failed:
                failed = True
                raise OSError("secret group")
            real_killpg(pid, sig)

        monkeypatch.setattr(process_module, "_killpg", faulty_killpg)

    caught = _run_tracked_failure(
        monkeypatch,
        (executable,),
        deadline=time.monotonic() + 0.15,
        term_grace_seconds=0.1,
        kill_grace_seconds=0.2,
    )
    assert caught.reason in {"timeout", "io"}
    assert "secret" not in str(caught)
    assert "secret" not in repr(caught)


@pytest.mark.parametrize(
    "fault",
    ("factory", "register", "unregister", "get_map", "close"),
)
def test_one_shot_selector_lifecycle_faults_are_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    executable = _write_executable(tmp_path / "exit", "")
    real_factory = process_module._selector_factory
    signals: list[int] = []

    def killpg(pid: int, sig: int) -> None:
        signals.append(sig)
        os.killpg(pid, sig)

    class FaultySelector:
        def __init__(self) -> None:
            self.inner = real_factory()
            self.failed = False

        def __getattr__(self, name: str) -> object:
            inner = getattr(self.inner, name)

            def call(*args: object) -> object:
                if name == fault and not self.failed:
                    self.failed = True
                    raise OSError("secret selector lifecycle")
                return inner(*args)

            return call

    if fault == "factory":
        failed = False

        def factory() -> object:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("secret factory")
            return FaultySelector()

        monkeypatch.setattr(process_module, "_selector_factory", factory)
    else:
        monkeypatch.setattr(process_module, "_selector_factory", FaultySelector)
    monkeypatch.setattr(process_module, "_killpg", killpg)
    descriptors = _fd_count()
    caught = _run_tracked_failure(monkeypatch, (executable,))
    assert _fd_count() == descriptors
    assert caught.reason in {"io", "teardown"}
    if fault == "close":
        assert signal.SIGKILL not in signals


def test_post_exit_probe_fault_is_teardown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _write_executable(tmp_path / "exit", "")
    real_killpg = os.killpg
    failed = False

    def faulty_probe(pid: int, sig: int) -> None:
        nonlocal failed
        if sig == 0 and not failed:
            failed = True
            raise OSError("ambiguous")
        real_killpg(pid, sig)

    monkeypatch.setattr(process_module, "_killpg", faulty_probe)
    caught = _run_tracked_failure(monkeypatch, (executable,))
    assert caught.reason == "teardown"


def test_interruption_after_spawn_cleans_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _write_executable(
        tmp_path / "sleep",
        "import time\ntime.sleep(10)\n",
    )
    groups: list[int] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        groups.append(process.pid)
        return process

    monkeypatch.setattr(process_module, "_popen", recording_popen)
    monkeypatch.setattr(
        process_module,
        "_selector_factory",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            _run((executable,))
        assert groups
        with pytest.raises(ProcessLookupError):
            os.killpg(groups[0], 0)
    finally:
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


class FakeClock:
    def __init__(self, value: float, fail_once: bool = False) -> None:
        self.value = value
        self.fail_once = int(fail_once)

    def monotonic(self) -> float:
        if self.fail_once and self.value >= 30.0:
            self.fail_once += 1
            if self.fail_once > 2:
                self.fail_once = 0
                raise OSError("secret clock")
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(seconds, 0.001)


@pytest.mark.parametrize(
    "fault_mode",
    ("none", "persistent", "clock", "wait-once", "wait-persistent", "interrupt"),
)
def test_cleanup_graces_and_primary_precedence(
    monkeypatch: pytest.MonkeyPatch,
    fault_mode: str,
) -> None:
    clock = FakeClock(29.9, fail_once=fault_mode == "clock")
    signals: list[tuple[float, int]] = []
    wait_attempts: list[int] = []
    reaped = False

    class EmptySelector:
        def register(self, *args: object) -> None:
            pass

        def get_map(self) -> dict[object, object]:
            if fault_mode == "persistent":
                raise OSError("secret map")
            return {}

        def select(self, timeout: float) -> list[object]:
            clock.sleep(timeout)
            return []

        def close(self) -> None:
            if fault_mode == "persistent":
                raise OSError("secret close")

    class FakeProcess:
        pid = 123

        class Stream:
            closed = False

            def fileno(self) -> int:
                return 999

            def close(self) -> None:
                self.closed = True

        stdout = Stream()
        stderr = Stream()

        def poll(self) -> None:
            return 0 if reaped else None

        def wait(self, timeout: float) -> None:
            nonlocal reaped
            wait_attempts.append(1)
            if fault_mode == "wait-once" and len(wait_attempts) > 1:
                reaped = True
                return
            clock.sleep(min(timeout, 0.01))
            raise OSError("secret wait")

    def killpg(pid: int, sig: int) -> None:
        signals.append((clock.value, sig))
        if fault_mode == "interrupt" and sig == signal.SIGTERM:
            raise KeyboardInterrupt
        if fault_mode == "persistent":
            raise OSError("secret signal")
        if sig == 0 and fault_mode.startswith("wait-"):
            raise ProcessLookupError

    monkeypatch.setattr(process_module, "_monotonic", clock.monotonic)
    monkeypatch.setattr(process_module, "_sleep", clock.sleep)
    monkeypatch.setattr(process_module, "_selector_factory", EmptySelector)
    monkeypatch.setattr(process_module, "_popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(process_module, "_killpg", killpg)
    monkeypatch.setattr(process_module.os, "set_blocking", lambda *a: None)
    with pytest.raises(BoundedProcessFailure) as caught:
        _run(
            ("/usr/bin/fake",),
            deadline=30.0,
            term_grace_seconds=0.5,
            kill_grace_seconds=0.5,
        )
    assert caught.value.reason == "timeout"
    delivered = [(at, sig) for at, sig in signals if sig != 0]
    assert delivered[0] == (30.0, signal.SIGTERM)
    if fault_mode == "wait-once":
        assert reaped and len(wait_attempts) == 2
    elif fault_mode == "wait-persistent":
        assert not reaped and 1 < len(wait_attempts) <= 128
    else:
        assert delivered[1][0] <= 30.5
        assert delivered[1][1] == signal.SIGKILL
    assert clock.value <= 31.0
