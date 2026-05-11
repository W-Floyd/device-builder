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
import os
import signal
import sys
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
async def test_unix_watcher_cancels_cleanly_mid_sleep() -> None:
    """Cancelling the task while it's sleeping exits without firing shutdown."""
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
        # The watcher catches CancelledError and returns cleanly,
        # so the awaiting gather sees a normal completion.
        await asyncio.gather(task, return_exceptions=True)

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

    fake_wait.assert_called_once_with(1234)
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
