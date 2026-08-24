from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal

FailureReason = Literal["spawn", "timeout", "output", "io", "teardown"]

_REAL_MONOTONIC = time.monotonic
_monotonic = time.monotonic
_sleep = time.sleep
_killpg = os.killpg
_read = os.read
_popen = subprocess.Popen
_selector_factory = selectors.DefaultSelector
_POLL_SECONDS = 0.01
_MAX_PHASE_ATTEMPTS = 128


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class BoundedProcessFailure(RuntimeError):
    reason: FailureReason

    def __init__(self, *, reason: FailureReason) -> None:
        self.reason = reason
        super().__init__(reason)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(reason={self.reason!r})"


def _validate(
    argv: tuple[str, ...],
    cwd: str,
    environment: dict[str, str],
    deadline: float,
    stdout_limit: int,
    stderr_limit: int,
    aggregate_limit: int,
    term_grace_seconds: float,
    kill_grace_seconds: float,
    pass_fds: tuple[int, ...],
) -> None:
    if (
        type(argv) is not tuple
        or not argv
        or any(type(value) is not str or not value for value in argv)
    ):
        raise ValueError("invalid process argv")
    if (
        type(cwd) is not str
        or not os.path.isabs(cwd)
        or cwd.startswith("//")
        or os.path.normpath(cwd) != cwd
    ):
        raise ValueError("invalid process cwd")
    if type(environment) is not dict or any(
        type(key) is not str or not key or type(value) is not str
        for key, value in environment.items()
    ):
        raise ValueError("invalid process environment")
    if type(deadline) is not float or not math.isfinite(deadline):
        raise ValueError("invalid process deadline")
    limits = (stdout_limit, stderr_limit, aggregate_limit)
    if (
        any(type(limit) is not int or limit <= 0 for limit in limits)
        or aggregate_limit < max(stdout_limit, stderr_limit)
        or aggregate_limit > stdout_limit + stderr_limit
    ):
        raise ValueError("invalid process output limits")
    if any(
        type(grace) is not float or not math.isfinite(grace) or grace < 0
        for grace in (term_grace_seconds, kill_grace_seconds)
    ):
        raise ValueError("invalid process cleanup grace")
    if (
        type(pass_fds) is not tuple
        or len(pass_fds) != len(set(pass_fds))
        or any(type(fd) is not int or fd < 0 for fd in pass_fds)
    ):
        raise ValueError("invalid process pass fds")


def _group_gone(process_group: int) -> bool:
    try:
        _killpg(process_group, 0)
    except ProcessLookupError:
        return True
    return False


def _close_one(selector: selectors.BaseSelector, stream: Any) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass
    stream.close()


def _drain(
    selector: selectors.BaseSelector,
    stdout: bytearray,
    stderr: bytearray,
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    aggregate_limit: int,
) -> FailureReason | None:
    for key, _ in selector.select(max(0.0, timeout)):
        stream = key.fileobj
        try:
            chunk = _read(stream.fileno(), 65_536)
        except BlockingIOError:
            continue
        if not chunk:
            _close_one(selector, stream)
            continue
        target, other, limit = (
            (stdout, stderr, stdout_limit)
            if key.data == "stdout"
            else (stderr, stdout, stderr_limit)
        )
        room = min(limit - len(target), aggregate_limit - len(target) - len(other))
        if room >= 0:
            target.extend(chunk[: room + 1])
        if len(target) > limit or len(target) + len(other) > aggregate_limit:
            return "output"
    return None


def _attempt(
    operation: Any,
    default: Any,
    interrupted: list[tuple[BaseException, TracebackType | None]],
) -> tuple[Any, bool]:
    try:
        return operation(), True
    except BaseException as caught:  # noqa: BLE001 - teardown must continue.
        if not isinstance(caught, Exception) and not interrupted:
            interrupted.append((caught, caught.__traceback__))
        return default, False


def _cleanup(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector | None,
    *,
    logical_start_hint: float,
    term_grace_seconds: float,
    kill_grace_seconds: float,
) -> tuple[bool, bool, tuple[BaseException, TracebackType | None] | None]:
    interrupted: list[tuple[BaseException, TracebackType | None]] = []
    real_start = _REAL_MONOTONIC()
    logical_start, clock_ok = _attempt(_monotonic, logical_start_hint, interrupted)
    term_end = logical_start + term_grace_seconds
    kill_end = term_end + kill_grace_seconds
    real_term_end = real_start + term_grace_seconds
    real_kill_end = real_term_end + kill_grace_seconds
    clean = bool(clock_ok)

    def now() -> float:
        nonlocal clean
        value, ok = _attempt(
            _monotonic,
            logical_start + (_REAL_MONOTONIC() - real_start),
            interrupted,
        )
        clean = clean and ok
        return value

    def probe() -> bool:
        nonlocal clean
        gone, ok = _attempt(lambda: _group_gone(process.pid), False, interrupted)
        clean = clean and ok
        return bool(gone)

    def send(sig: int) -> bool:
        nonlocal clean

        def operation() -> bool:
            try:
                _killpg(process.pid, sig)
            except ProcessLookupError:
                return True
            return False

        gone, ok = _attempt(operation, False, interrupted)
        clean = clean and ok
        return bool(gone)

    def phase(logical_end: float, real_end: float) -> bool:
        nonlocal clean
        gone = False
        for _ in range(_MAX_PHASE_ATTEMPTS):
            if gone or now() >= logical_end or _REAL_MONOTONIC() >= real_end:
                break
            if selector is not None:
                remaining = min(
                    _POLL_SECONDS,
                    max(0.0, logical_end - now()),
                    max(0.0, real_end - _REAL_MONOTONIC()),
                )
                _, ok = _attempt(
                    lambda remaining=remaining: _drain(
                        selector,
                        bytearray(),
                        bytearray(),
                        timeout=remaining,
                        stdout_limit=1,
                        stderr_limit=1,
                        aggregate_limit=2,
                    ),
                    None,
                    interrupted,
                )
                clean = clean and ok
            _, ok = _attempt(process.poll, None, interrupted)
            clean = clean and ok
            gone = probe()
            if not gone:
                _, ok = _attempt(
                    lambda: _sleep(min(_POLL_SECONDS, max(0.0, logical_end - now()))),
                    None,
                    interrupted,
                )
                clean = clean and ok
        return gone

    gone = send(signal.SIGTERM)
    if not gone:
        gone = phase(term_end, real_term_end)
    if not gone:
        gone = send(signal.SIGKILL)
    if not gone:
        gone = phase(kill_end, real_kill_end)

    if selector is not None:
        mapped, ok = _attempt(
            lambda: tuple(selector.get_map().values()),
            (),
            interrupted,
        )
        clean = clean and ok
        for key in mapped:
            _, ok = _attempt(
                lambda key=key: _close_one(selector, key.fileobj),
                None,
                interrupted,
            )
            clean = clean and ok
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            _, ok = _attempt(stream.close, None, interrupted)
            clean = clean and ok
    if selector is not None:
        _, ok = _attempt(selector.close, None, interrupted)
        clean = clean and ok

    reaped = False
    for _ in range(_MAX_PHASE_ATTEMPTS):
        polled, ok = _attempt(process.poll, None, interrupted)
        clean = clean and ok
        if ok and polled is not None:
            reaped = True
            break
        remaining = min(
            max(0.0, kill_end - now()),
            max(0.0, real_kill_end - _REAL_MONOTONIC()),
        )
        if remaining <= 0:
            break
        _, ok = _attempt(
            lambda remaining=remaining: process.wait(timeout=min(_POLL_SECONDS, remaining)),
            None,
            interrupted,
        )
        clean = clean and ok
        if ok:
            reaped = True
            break
    clean = clean and reaped
    gone = probe()
    return clean, gone, interrupted[0] if interrupted else None


def run_bounded_process(
    argv: tuple[str, ...],
    *,
    cwd: str,
    environment: dict[str, str],
    deadline: float,
    stdout_limit: int,
    stderr_limit: int,
    aggregate_limit: int,
    term_grace_seconds: float,
    kill_grace_seconds: float,
    pass_fds: tuple[int, ...] = (),
) -> BoundedProcessResult:
    """Run one command or fail after bounded best-effort group teardown."""
    _validate(
        argv,
        cwd,
        environment,
        deadline,
        stdout_limit,
        stderr_limit,
        aggregate_limit,
        term_grace_seconds,
        kill_grace_seconds,
        pass_fds,
    )
    observed_now = _monotonic()
    if deadline <= observed_now:
        raise BoundedProcessFailure(reason="timeout")
    try:
        descriptor_arguments = {"pass_fds": pass_fds} if pass_fds else {}
        process = _popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            close_fds=True,
            start_new_session=True,
            bufsize=0,
            **descriptor_arguments,
        )
    except Exception:  # noqa: BLE001 - stable spawn failure boundary.
        raise BoundedProcessFailure(reason="spawn") from None

    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    primary: FailureReason | None = None
    result: BoundedProcessResult | None = None
    interrupted: tuple[BaseException, TracebackType | None] | None = None
    try:
        selector = _selector_factory()
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            if stream is None:
                raise OSError
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while True:
            now = _monotonic()
            observed_now = now
            if now >= deadline:
                primary = "timeout"
                break
            primary = _drain(
                selector,
                stdout,
                stderr,
                timeout=min(_POLL_SECONDS, deadline - now),
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
                aggregate_limit=aggregate_limit,
            )
            if primary is not None:
                break
            returncode = process.poll()
            if returncode is None or selector.get_map():
                continue
            try:
                gone = _group_gone(process.pid)
            except Exception:  # noqa: BLE001 - ambiguous ownership fails closed.
                primary = "teardown"
                break
            if not gone:
                primary = "teardown"
                break
            selector.close()
            selector = None
            result = BoundedProcessResult(returncode, bytes(stdout), bytes(stderr))
            break
    except Exception:  # noqa: BLE001 - stable operational failure boundary.
        primary = primary or "io"
    except BaseException as caught:  # noqa: BLE001 - clean before propagation.
        interrupted = (caught, caught.__traceback__)

    if result is None or interrupted is not None:
        _cleanup(
            process,
            selector,
            logical_start_hint=observed_now,
            term_grace_seconds=term_grace_seconds,
            kill_grace_seconds=kill_grace_seconds,
        )
    if interrupted is not None:
        caught, traceback = interrupted
        raise caught.with_traceback(traceback)
    if result is not None:
        return result
    reason = primary if primary in {"timeout", "output", "io"} else "teardown"
    raise BoundedProcessFailure(reason=reason)
