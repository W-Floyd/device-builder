"""
Tests for ``helpers.parent_watchdog``.

Cover the four observable behaviours the watchdog has to get right:

1. :func:`should_engage` honours an explicit force flag and falls
   back to the desktop-app launcher signature when none is given.
2. The async watcher returns immediately when there's no useful
   parent (PID 1).
3. The async watcher detects ppid changes on Unix and signals
   self-shutdown.
4. The async watcher is cancellable cleanly while parked in its
   sleep.

The Windows code path (handle-based wait via ctypes) is exercised
through a mock — actually opening a process handle in tests is
both flaky on CI and impossible cross-platform; the unit-level
coverage pins the dispatch shape and the shutdown trigger so
regressions stay caught.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from esphome_device_builder.helpers import parent_watchdog

# ---------------------------------------------------------------------------
# should_engage
# ---------------------------------------------------------------------------


def test_should_engage_force_true_overrides_detection() -> None:
    """Explicit ``True`` engages even when not running under the desktop app."""
    with patch.object(parent_watchdog, "_spawned_by_desktop_app", return_value=False):
        assert parent_watchdog.should_engage(force=True) is True


def test_should_engage_force_false_overrides_detection() -> None:
    """Explicit ``False`` disables even when running under the desktop app."""
    with patch.object(parent_watchdog, "_spawned_by_desktop_app", return_value=True):
        assert parent_watchdog.should_engage(force=False) is False


def test_should_engage_none_falls_through_to_auto_detect() -> None:
    """``None`` defers to the launcher signature."""
    with patch.object(parent_watchdog, "_spawned_by_desktop_app", return_value=True):
        assert parent_watchdog.should_engage(force=None) is True
    with patch.object(parent_watchdog, "_spawned_by_desktop_app", return_value=False):
        assert parent_watchdog.should_engage(force=None) is False


def test_spawned_by_desktop_app_matches_bundle_id() -> None:
    """The detector matches when the bundle id appears in ``sys.executable``.

    Pins the bundle-id matcher: a Python interpreter whose path
    lives under the macOS / Windows app-support directory keyed
    on ``io.esphome.builder`` is treated as a desktop-app spawn.
    Other paths (system Python, virtualenv, HA addon's bundled
    Python) don't match and skip the watchdog.
    """
    desktop_path = "/Users/me/Library/Application Support/io.esphome.builder/python/bin/python3"
    with patch.object(sys, "executable", desktop_path):
        assert parent_watchdog._spawned_by_desktop_app() is True

    with patch.object(sys, "executable", "/usr/local/bin/python3"):
        assert parent_watchdog._spawned_by_desktop_app() is False

    # HA addon path — bundle id is absent.
    with patch.object(sys, "executable", "/usr/local/bin/python3.13"):
        assert parent_watchdog._spawned_by_desktop_app() is False


# ---------------------------------------------------------------------------
# watch_parent_and_exit_on_death — early-return paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_returns_immediately_when_already_orphan() -> None:
    """ppid==1 means we were never going to be supervised; no-op cleanly."""
    triggered: list[None] = []
    with (
        patch.object(parent_watchdog.os, "getppid", return_value=1),
        patch.object(
            parent_watchdog,
            "_trigger_self_shutdown",
            side_effect=lambda: triggered.append(None),
        ),
    ):
        await asyncio.wait_for(parent_watchdog.watch_parent_and_exit_on_death(), timeout=1.0)

    assert triggered == []


@pytest.mark.asyncio
async def test_watchdog_returns_immediately_when_already_orphan_ppid_zero() -> None:
    """Defensive: a ``getppid() == 0`` (BSD-ish edge case) also skips."""
    triggered: list[None] = []
    with (
        patch.object(parent_watchdog.os, "getppid", return_value=0),
        patch.object(
            parent_watchdog,
            "_trigger_self_shutdown",
            side_effect=lambda: triggered.append(None),
        ),
    ):
        await asyncio.wait_for(parent_watchdog.watch_parent_and_exit_on_death(), timeout=1.0)

    assert triggered == []


# ---------------------------------------------------------------------------
# Unix polling path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only polling path")
@pytest.mark.asyncio
async def test_unix_watcher_triggers_shutdown_on_reparent() -> None:
    """When ``os.getppid()`` returns a new value, shutdown signal fires.

    Simulates the kernel reparenting the dashboard under
    ``launchd`` (or PID 1 / a subreaper on Linux) when the
    Tauri parent dies. The watcher observes the ppid change on
    its next poll and triggers self-shutdown — the aiohttp
    SIGTERM handler then runs the usual cleanup chain.
    """
    # First call captures the "original" parent (mock returns
    # 1234); subsequent calls (inside the polling loop) return
    # 1, simulating the reparent.
    ppids = iter([1234, 1, 1, 1])
    triggered: list[None] = []

    with (
        patch.object(parent_watchdog.os, "getppid", side_effect=lambda: next(ppids)),
        patch.object(
            parent_watchdog,
            "_trigger_self_shutdown",
            side_effect=lambda: triggered.append(None),
        ),
    ):
        await asyncio.wait_for(
            parent_watchdog.watch_parent_and_exit_on_death(poll_seconds=0.01),
            timeout=1.0,
        )

    assert triggered == [None]


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only polling path")
@pytest.mark.asyncio
async def test_unix_watcher_polls_until_change_then_exits() -> None:
    """No shutdown fires while ppid stays stable; loop exits on change."""
    # Original ppid, then several "still alive" reads, then the reparent.
    ppids = iter([1234, 1234, 1234, 1234, 5678])
    triggered: list[None] = []

    with (
        patch.object(parent_watchdog.os, "getppid", side_effect=lambda: next(ppids)),
        patch.object(
            parent_watchdog,
            "_trigger_self_shutdown",
            side_effect=lambda: triggered.append(None),
        ),
    ):
        await asyncio.wait_for(
            parent_watchdog.watch_parent_and_exit_on_death(poll_seconds=0.01),
            timeout=1.0,
        )

    assert triggered == [None]


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only polling path")
@pytest.mark.asyncio
async def test_unix_watcher_propagates_cancellation_without_firing_shutdown() -> None:
    """Cancellation surfaces as ``CancelledError`` and does not fire shutdown.

    Pins the no-suppress-CancelledError contract documented in
    ``_watch_unix``'s docstring: the watchdog must propagate
    cancellation so callers awaiting the task directly can
    distinguish "cancelled" from "parent died and we shut down".
    Suppressing here would silently regress that distinction
    even though ``gather(..., return_exceptions=True)`` happens
    to handle both the same way at the only current callsite.
    """
    triggered: list[None] = []

    with (
        patch.object(parent_watchdog.os, "getppid", return_value=1234),
        patch.object(
            parent_watchdog,
            "_trigger_self_shutdown",
            side_effect=lambda: triggered.append(None),
        ),
    ):
        task = asyncio.create_task(
            parent_watchdog.watch_parent_and_exit_on_death(poll_seconds=10.0)
        )
        # Let the task enter its first sleep before cancelling.
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert triggered == []


# ---------------------------------------------------------------------------
# Windows dispatch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_windows_dispatch_calls_handle_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows the watcher dispatches to the handle-based blocker.

    Pins the platform fork: ``sys.platform == "win32"`` routes
    through ``_watch_windows`` rather than the polling loop.
    Forcing the platform string and stubbing the blocking wait
    keeps the assertion deterministic regardless of where the
    test runs.
    """
    monkeypatch.setattr(parent_watchdog.sys, "platform", "win32")
    monkeypatch.setattr(parent_watchdog.os, "getppid", lambda: 1234)
    triggered: list[None] = []
    monkeypatch.setattr(
        parent_watchdog,
        "_trigger_self_shutdown",
        lambda: triggered.append(None),
    )

    fake_wait = MagicMock(return_value=True)
    monkeypatch.setattr(parent_watchdog, "_wait_for_parent_handle_windows", fake_wait)

    await asyncio.wait_for(parent_watchdog.watch_parent_and_exit_on_death(), timeout=1.0)

    # ``_wait_for_parent_handle_windows`` is called with the parent
    # pid + a ``threading.Event`` the wrapper owns. We don't care
    # about the event identity, just that the pid arg is right.
    fake_wait.assert_called_once()
    args, _ = fake_wait.call_args
    assert args[0] == 1234
    assert isinstance(args[1], threading.Event)
    assert triggered == [None]


@pytest.mark.asyncio
async def test_windows_dispatch_no_shutdown_when_handle_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``OpenProcess`` leaves the watcher inactive (no shutdown)."""
    monkeypatch.setattr(parent_watchdog.sys, "platform", "win32")
    monkeypatch.setattr(parent_watchdog.os, "getppid", lambda: 1234)
    triggered: list[None] = []
    monkeypatch.setattr(
        parent_watchdog,
        "_trigger_self_shutdown",
        lambda: triggered.append(None),
    )

    # Handle couldn't be opened — should fall through without
    # firing the shutdown trigger (otherwise a missing-handle
    # race at startup would kill the dashboard prematurely).
    monkeypatch.setattr(
        parent_watchdog,
        "_wait_for_parent_handle_windows",
        MagicMock(return_value=False),
    )

    await asyncio.wait_for(parent_watchdog.watch_parent_and_exit_on_death(), timeout=1.0)

    assert triggered == []


@pytest.mark.asyncio
async def test_windows_dispatch_sets_cancel_event_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the Windows watcher signals the worker thread to stop.

    Pins the fix for the thread-leak bug: without setting the
    ``cancel_event`` on the way out, the worker thread would be
    parked in ``WaitForSingleObject`` forever (the previous
    ``INFINITE`` shape), hanging dashboard shutdown until the
    parent actually died. The new ``_watch_windows`` catches
    ``CancelledError``, sets the event, then re-raises so the
    asyncio side cancels cleanly and the kernel-thread side
    exits on its next poll tick (up to ``_WIN_POLL_MS`` later).
    """
    monkeypatch.setattr(parent_watchdog.sys, "platform", "win32")
    monkeypatch.setattr(parent_watchdog.os, "getppid", lambda: 1234)

    # Capture the event so the test can assert it got set, and
    # block the worker until the event fires so the cancel path
    # is the only way the function returns.
    captured_events: list[threading.Event] = []

    def _blocking_wait(_pid: int, cancel_event: threading.Event) -> bool:
        captured_events.append(cancel_event)
        # Wait up to 5 s for the test to signal cancel. If the
        # event never gets set, the test fails by timeout.
        cancel_event.wait(timeout=5.0)
        return False

    monkeypatch.setattr(parent_watchdog, "_wait_for_parent_handle_windows", _blocking_wait)

    task = asyncio.create_task(parent_watchdog.watch_parent_and_exit_on_death())
    # Let the task hand off to the worker thread.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(captured_events) == 1
    # The wrapper must have set the event so the worker thread
    # can break out of its polling loop.
    assert captured_events[0].is_set()


# ---------------------------------------------------------------------------
# _trigger_self_shutdown
# ---------------------------------------------------------------------------


def test_trigger_self_shutdown_unix_sends_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unix shutdown trigger calls ``os.kill(self, SIGTERM)``."""
    monkeypatch.setattr(parent_watchdog.sys, "platform", "darwin")
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(parent_watchdog.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    parent_watchdog._trigger_self_shutdown()

    assert calls == [(os.getpid(), signal.SIGTERM)]


def test_trigger_self_shutdown_windows_sends_ctrl_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows shutdown trigger uses ``CTRL_BREAK_EVENT`` (the aiohttp-handled signal)."""
    monkeypatch.setattr(parent_watchdog.sys, "platform", "win32")
    calls: list[tuple[int, int]] = []
    # Real ``signal.CTRL_BREAK_EVENT`` only exists on Windows; the
    # module references it via attribute lookup so we have to
    # provide a stand-in on Unix test runners.
    fake_break = getattr(signal, "CTRL_BREAK_EVENT", 1)
    # ``raising=False`` lets us inject the attribute on test runners
    # where the real ``CTRL_BREAK_EVENT`` isn't exported (it's
    # Windows-only); without it monkeypatch refuses the setattr.
    monkeypatch.setattr(parent_watchdog.signal, "CTRL_BREAK_EVENT", fake_break, raising=False)
    monkeypatch.setattr(parent_watchdog.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    parent_watchdog._trigger_self_shutdown()

    assert calls == [(os.getpid(), fake_break)]


def test_trigger_self_shutdown_falls_back_to_hard_exit_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``os.kill`` raises ``OSError``, drop to ``os._exit(0)``.

    Pins the safety-net branch: if signal delivery to ourselves
    fails for any reason (signal masked, kernel quirk, etc), the
    watchdog must still exit the process. Leaking the listening
    port forever is strictly worse than a hard exit that skips
    aiohttp's cleanup chain.
    """
    monkeypatch.setattr(parent_watchdog.sys, "platform", "darwin")

    def _raise_oserror(_pid: int, _sig: int) -> None:
        raise OSError("simulated kernel error")

    exit_calls: list[int] = []
    monkeypatch.setattr(parent_watchdog.os, "kill", _raise_oserror)
    monkeypatch.setattr(parent_watchdog.os, "_exit", exit_calls.append)

    parent_watchdog._trigger_self_shutdown()

    assert exit_calls == [0]


# ---------------------------------------------------------------------------
# Windows ctypes path — drive _wait_for_parent_handle_windows directly
# ---------------------------------------------------------------------------


def _make_fake_kernel32(
    *,
    open_handle: int,
    wait_result: int,
) -> object:
    """Build a stand-in for ``ctypes.WinDLL("kernel32")`` returning canned values."""
    fake = MagicMock()
    fake.OpenProcess = MagicMock(return_value=open_handle)
    fake.WaitForSingleObject = MagicMock(return_value=wait_result)
    fake.CloseHandle = MagicMock(return_value=1)
    return fake


def test_wait_for_parent_handle_windows_returns_true_on_signalled_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful ``OpenProcess`` + ``WaitForSingleObject`` → True (parent exited).

    Drives the Windows ctypes path directly so the function body
    gets coverage. ``ctypes.WinDLL`` only exists on real Windows
    runs, so we stub it via ``setattr(raising=False)`` and patch
    the module's own ``import ctypes`` to return our stub.
    """
    fake_handle = 0xDEAD_BEEF
    fake_kernel32 = _make_fake_kernel32(
        open_handle=fake_handle,
        wait_result=parent_watchdog._WIN_WAIT_OBJECT_0,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: fake_kernel32, raising=False)

    result = parent_watchdog._wait_for_parent_handle_windows(1234, threading.Event())

    assert result is True
    fake_kernel32.OpenProcess.assert_called_once_with(
        parent_watchdog._WIN_PROCESS_SYNCHRONIZE, False, 1234
    )
    # Polling timeout is the per-tick value, not INFINITE — see
    # ``_WIN_POLL_MS`` for the cadence rationale.
    fake_kernel32.WaitForSingleObject.assert_called_once_with(
        fake_handle, parent_watchdog._WIN_POLL_MS
    )
    # Handle must be closed even on the happy path so the kernel
    # isn't left holding a stale reference.
    fake_kernel32.CloseHandle.assert_called_once_with(fake_handle)


def test_wait_for_parent_handle_windows_returns_false_when_handle_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NULL handle from ``OpenProcess`` → False (watchdog inactive).

    Distinguishes "parent died" from "couldn't observe parent" — the
    caller treats False as the latter so we don't race an immediate
    self-shutdown at startup if the parent handle wasn't openable
    (insufficient permissions, parent already gone).
    """
    fake_kernel32 = _make_fake_kernel32(
        open_handle=0,  # NULL — OpenProcess failure
        wait_result=parent_watchdog._WIN_WAIT_OBJECT_0,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: fake_kernel32, raising=False)

    result = parent_watchdog._wait_for_parent_handle_windows(1234, threading.Event())

    assert result is False
    # When OpenProcess fails we must NOT touch WaitForSingleObject /
    # CloseHandle — passing a NULL handle to either would be a
    # programmer error on the Win32 side.
    fake_kernel32.WaitForSingleObject.assert_not_called()
    fake_kernel32.CloseHandle.assert_not_called()


def test_wait_for_parent_handle_windows_returns_false_on_unexpected_wait_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``WAIT_FAILED`` / abandoned return bails the polling loop.

    Defensive: if ``WaitForSingleObject`` ever returns a status
    that isn't ``WAIT_OBJECT_0`` or ``WAIT_TIMEOUT``, we treat
    it as "can't observe" rather than "parent died" — same
    rationale as the open-failure case. Without this branch a
    kernel quirk could kick the dashboard offline at startup.
    """
    fake_handle = 0xCAFE
    fake_kernel32 = _make_fake_kernel32(
        open_handle=fake_handle,
        wait_result=0xFFFFFFFF,  # WAIT_FAILED
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: fake_kernel32, raising=False)

    result = parent_watchdog._wait_for_parent_handle_windows(1234, threading.Event())

    assert result is False
    fake_kernel32.CloseHandle.assert_called_once_with(fake_handle)


def test_wait_for_parent_handle_windows_continues_polling_on_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WAIT_TIMEOUT`` keeps polling; ``cancel_event`` breaks the loop.

    Pins the new polling shape: ``WaitForSingleObject`` with a
    finite ``_WIN_POLL_MS`` timeout returns ``WAIT_TIMEOUT``
    every tick while the parent is still alive. The function
    must loop (not bail) on that status, and observe the
    ``cancel_event`` between iterations so dashboard shutdown
    can break out without waiting for the parent to die. The
    previous ``INFINITE`` shape couldn't do this — the worker
    thread would have leaked past dashboard shutdown.
    """
    fake_handle = 0xC0FFEE
    fake_kernel32 = _make_fake_kernel32(
        open_handle=fake_handle,
        wait_result=parent_watchdog._WIN_WAIT_TIMEOUT,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: fake_kernel32, raising=False)

    cancel_event = threading.Event()
    # ``side_effect`` returns ``WAIT_TIMEOUT`` for the first two
    # calls, then we flip the cancel flag and the third (if it
    # ever runs) would have returned the same — but the loop
    # checks the event BEFORE the next wait, so two ticks is the
    # maximum even without ever signalling the parent handle.
    call_count = 0

    def _wait_then_cancel(_handle: int, _timeout_ms: int) -> int:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            cancel_event.set()
        return parent_watchdog._WIN_WAIT_TIMEOUT

    fake_kernel32.WaitForSingleObject.side_effect = _wait_then_cancel

    result = parent_watchdog._wait_for_parent_handle_windows(1234, cancel_event)

    assert result is False
    # We expect the wait to be called exactly twice: first tick
    # returns WAIT_TIMEOUT and we loop; second tick returns
    # WAIT_TIMEOUT but the side_effect has set the event so the
    # next ``cancel_event.is_set()`` check exits the loop.
    assert call_count == 2
    fake_kernel32.CloseHandle.assert_called_once_with(fake_handle)
