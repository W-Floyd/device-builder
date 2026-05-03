"""Tests for ``helpers/process.py`` — subprocess teardown helpers.

The platform-specific halves (POSIX ``_signal_process_group`` and
Windows ``_terminate_subtree_windows``) are exercised by the
firmware-stop integration tests; this file covers the shared
helpers ``kill_quietly`` and ``terminate_subtree_with_grace`` that
sit on top of them.
"""

from __future__ import annotations

import signal
import sys
from typing import Any

import pytest

from esphome_device_builder.helpers.process import (
    kill_quietly,
    terminate_subtree_with_grace,
)

# ---------------------------------------------------------------------------
# kill_quietly
# ---------------------------------------------------------------------------


def test_kill_quietly_swallows_process_lookup_error() -> None:
    """A ``ProcessLookupError`` from ``proc.kill()`` doesn't propagate.

    Race shape: caller checks ``proc.returncode is None``, the
    child exits, the kill then raises because the pid is reaped.
    The helper wraps the kill so call sites don't repeat the
    suppress block — drop the suppress and this test surfaces it.
    """

    class _DeadProc:
        def kill(self) -> None:
            raise ProcessLookupError

    kill_quietly(_DeadProc())  # type: ignore[arg-type]


def test_kill_quietly_calls_kill_when_alive() -> None:
    """A live proc gets ``proc.kill()`` invoked exactly once."""

    class _LiveProc:
        def __init__(self) -> None:
            self.calls = 0

        def kill(self) -> None:
            self.calls += 1

    proc = _LiveProc()
    kill_quietly(proc)  # type: ignore[arg-type]
    assert proc.calls == 1


# ---------------------------------------------------------------------------
# terminate_subtree_with_grace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_subtree_with_grace_no_op_on_already_exited() -> None:
    """Already-exited proc returns immediately without trying to signal."""

    class _ExitedProc:
        returncode = 0
        pid = 99999

        def kill(self) -> None:  # pragma: no cover — must not be called
            raise AssertionError("kill() reached on an already-exited proc")

    await terminate_subtree_with_grace(_ExitedProc())  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal-group path")
@pytest.mark.asyncio
async def test_terminate_subtree_with_grace_sigterm_then_exit_no_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM is sent; child exits within grace → no SIGKILL escalation.

    Pin the happy POSIX path: process group gets a SIGTERM, the
    proc exits before the grace window closes, the SIGKILL branch
    is never reached. A regression that flipped the wait-for to
    raise (or skipped the wait entirely) would land in the
    SIGKILL branch and assert here.
    """
    sent: list[tuple[int, int]] = []

    def _fake_signal(pid: int, sig: int) -> bool:
        sent.append((pid, sig))
        return True

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process._signal_process_group",
        _fake_signal,
    )

    class _ExitsCleanly:
        returncode = None
        pid = 12345

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    await terminate_subtree_with_grace(_ExitsCleanly())  # type: ignore[arg-type]

    assert sent == [(12345, signal.SIGTERM)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal-group path")
@pytest.mark.asyncio
async def test_terminate_subtree_with_grace_escalates_to_sigkill_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM ignored past grace → SIGKILL fires.

    Production trigger: a hung gcc that doesn't honour SIGTERM.
    The user-visible contract is "Stop kills the build" — if the
    SIGKILL branch ever drops, the runner loop hangs waiting for
    a process that won't exit and the queue gets wedged.
    """
    sent: list[tuple[int, int]] = []

    def _fake_signal(pid: int, sig: int) -> bool:
        sent.append((pid, sig))
        return True

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process._signal_process_group",
        _fake_signal,
    )

    async def _raise_timeout(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process.asyncio.wait_for",
        _raise_timeout,
    )

    class _StubProc:
        returncode = None
        pid = 12345

        async def wait(self) -> int:  # pragma: no cover — wait_for short-circuits
            return 0

    await terminate_subtree_with_grace(
        _StubProc(),  # type: ignore[arg-type]
        grace_seconds=0.01,
        job_label="job test-1",
    )

    assert sent == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal-group path")
@pytest.mark.asyncio
async def test_terminate_subtree_with_grace_returns_when_sigterm_undelivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_signal_process_group`` returning False (proc gone) short-circuits.

    No point waiting for a grace window or escalating to SIGKILL
    if the SIGTERM never landed — the pid is already gone.
    """
    waited = False

    def _fake_signal(_pid: int, _sig: int) -> bool:
        return False

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process._signal_process_group",
        _fake_signal,
    )

    async def _record_wait_for(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        nonlocal waited
        waited = True

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process.asyncio.wait_for",
        _record_wait_for,
    )

    class _StubProc:
        returncode = None
        pid = 12345

        async def wait(self) -> int:  # pragma: no cover
            return 0

    await terminate_subtree_with_grace(_StubProc())  # type: ignore[arg-type]

    assert waited is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill path")
@pytest.mark.asyncio
async def test_terminate_subtree_with_grace_falls_back_to_proc_kill_on_taskkill_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: taskkill returning False triggers the ``proc.kill()`` fallback.

    ``taskkill`` may be missing (stripped from the image) or hung;
    the fallback ensures the parent at least dies so the runner
    loop can finalise the job. The ``proc.kill()`` is wrapped in
    ``kill_quietly`` so a race with the child's own exit doesn't
    raise.
    """

    async def _fake_terminate_subtree(_pid: int) -> bool:
        return False

    monkeypatch.setattr(
        "esphome_device_builder.helpers.process._terminate_subtree_windows",
        _fake_terminate_subtree,
    )

    class _StubProc:
        returncode = None
        pid = 12345
        kill_calls = 0

        def kill(self) -> None:
            self.kill_calls += 1

    proc = _StubProc()
    await terminate_subtree_with_grace(proc)  # type: ignore[arg-type]

    assert proc.kill_calls == 1
