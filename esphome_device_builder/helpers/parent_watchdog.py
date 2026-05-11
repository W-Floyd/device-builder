"""
Parent-process watchdog — exit cleanly when the launching process disappears.

The dashboard is normally spawned by a supervisor that takes care of
shutdown (HA addon's s6, systemd, the ESPHome Builder desktop app's
``daemon::stop`` path). For supervisors that crash, get force-quit,
or otherwise skip their cleanup hook, the dashboard is left holding
its listening socket / port — restarting the supervisor then fails
to bind, or a stale dashboard answers for the previous config_dir
until someone notices and kills it manually.

This module installs an optional async task that watches the
original parent process and signals SIGTERM (Unix) / SIGBREAK
(Windows) to ourselves the instant the parent disappears. The
aiohttp app's existing SIGTERM handler then runs the normal
shutdown chain, freeing the port cleanly.

Engagement is conservative — see :func:`should_engage`. We only
opt in when we have strong evidence the launcher actually wants
this behaviour, so a direct ``python -m esphome_device_builder``
from a terminal does nothing surprising.

Cross-platform implementation:

* **Unix** (macOS / Linux): poll :func:`os.getppid` every couple
  of seconds. When the parent dies the kernel reparents this
  process to PID 1 (or the configured subreaper), so the ppid
  value changes — that's our trigger.
* **Windows**: PPID does **not** change after parent exit on
  Windows (there's no init equivalent that adopts orphans), so
  polling ppid is useless. Open a synchronisation handle on the
  parent process and block on it from a worker thread — when
  the handle becomes signalled, the parent has exited. Falling
  back to :func:`os.kill` ``(pid, 0)`` won't work either:
  CPython on Windows routes that through ``TerminateProcess``,
  which would *kill* the parent rather than test for it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

_LOGGER = logging.getLogger(__name__)

# Markers in ``sys.executable`` that identify the dashboard as
# having been spawned by the ESPHome Builder desktop app. The
# desktop bundles its own Python interpreter under an
# app-support directory keyed on the macOS bundle id; the same
# id appears in the Linux / Windows install paths the Tauri
# bundler produces, so a single substring check covers all
# three platforms.
#
# Keep this list minimal — every entry here is a self-declared
# "yes, I want the watchdog" signal from a launcher.
_DESKTOP_SPAWN_MARKERS: tuple[str, ...] = ("io.esphome.builder",)

# Polling cadence for the Unix path. Two seconds keeps the
# response time tight enough that a force-quit doesn't leave
# the port held for long, while not waking the loop too often
# on idle dashboards.
_POLL_SECONDS = 2.0


def _spawned_by_desktop_app() -> bool:
    """Return True if our interpreter looks like a desktop-app spawn."""
    return any(marker in sys.executable for marker in _DESKTOP_SPAWN_MARKERS)


def should_engage(force: bool | None) -> bool:
    """
    Decide whether the parent watchdog should run.

    Tri-state:

    * ``True`` — the user passed ``--exit-on-parent-changed``
      explicitly; honour it regardless of launcher.
    * ``False`` — the user passed ``--no-exit-on-parent-changed``;
      stay off regardless of launcher.
    * ``None`` — no explicit flag; auto-engage when
      :func:`_spawned_by_desktop_app` matches.

    Other launchers (HA addon's s6, systemd unit, ad-hoc
    ``python -m`` from a terminal) stay un-watched by default —
    they have their own supervision and don't want the dashboard
    self-terminating on parent change.
    """
    if force is not None:
        return force
    return _spawned_by_desktop_app()


def _trigger_self_shutdown() -> None:
    """Signal ourselves so aiohttp's normal shutdown chain runs.

    SIGTERM on Unix runs ``web.run_app``'s installed handler,
    which kicks off the ``on_cleanup`` chain (drains the
    firmware queue, persists state, closes the WS sites). On
    Windows we use ``CTRL_BREAK_EVENT`` — the closest analogue
    aiohttp wires up; ``SIGTERM`` exists in :mod:`signal` but
    can't be delivered to ourselves there.
    """
    try:
        if sys.platform == "win32":
            os.kill(os.getpid(), signal.CTRL_BREAK_EVENT)
        else:
            os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        _LOGGER.exception("parent-watchdog: failed to deliver shutdown signal")
        # Best-effort hard exit — better than leaking the port forever.
        os._exit(0)


async def _watch_unix(parent_pid: int, *, poll_seconds: float) -> None:
    """Poll :func:`os.getppid`; trigger shutdown on reparent.

    When the original parent dies, the kernel reparents us
    under PID 1 (or a configured subreaper). The change is
    observable on the next ``getppid`` call — no signal
    handler or syscall hook required.
    """
    while True:
        try:
            await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:
            return
        current = os.getppid()
        if current != parent_pid:
            _LOGGER.warning(
                "parent-watchdog: parent %d gone (reparented to %d); shutting down",
                parent_pid,
                current,
            )
            _trigger_self_shutdown()
            return


# Windows API constants used by :func:`_wait_for_parent_handle_windows`.
# Hoisted to module level so ruff's N806 (lowercase-in-function) doesn't
# complain — these names are upstream Win32 macros and stay uppercase
# by convention.
_WIN_PROCESS_SYNCHRONIZE = 0x00100000
_WIN_INFINITE = 0xFFFFFFFF
_WIN_WAIT_OBJECT_0 = 0


def _wait_for_parent_handle_windows(parent_pid: int) -> bool:
    """Block on the parent's process handle until it exits.

    Returns True when the parent exited (i.e. the handle was
    signalled); False if we couldn't open the handle in the
    first place. Synchronous on purpose — meant to run inside
    :func:`asyncio.to_thread` so the asyncio loop stays free.
    """
    import ctypes  # noqa: PLC0415 — Windows-only path, deferred to keep import cost off non-Windows

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_WIN_PROCESS_SYNCHRONIZE, False, parent_pid)
    if not handle:
        # No permission or process already gone — caller
        # should treat this as "watchdog couldn't engage", not
        # "parent died". Returning False keeps us from racing
        # an immediate self-shutdown on startup.
        return False
    try:
        result = kernel32.WaitForSingleObject(handle, _WIN_INFINITE)
        return result == _WIN_WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


async def _watch_windows(parent_pid: int) -> None:
    """Block-in-thread on the parent's handle; trigger shutdown on exit."""
    parent_exited = await asyncio.to_thread(_wait_for_parent_handle_windows, parent_pid)
    if parent_exited:
        _LOGGER.warning(
            "parent-watchdog: parent %d exited (handle signalled); shutting down",
            parent_pid,
        )
        _trigger_self_shutdown()
    else:
        _LOGGER.debug(
            "parent-watchdog: couldn't open handle for parent %d; watchdog inactive",
            parent_pid,
        )


async def watch_parent_and_exit_on_death(
    *,
    poll_seconds: float = _POLL_SECONDS,
) -> None:
    """
    Watchdog entry point. Picks the right primitive per platform.

    Returns cleanly (no action) when there's no useful parent
    to watch — already running under PID 1 on Unix, or unable
    to open a handle on Windows. Otherwise runs until the
    parent dies (triggers self-shutdown) or the task is
    cancelled (normal dashboard shutdown).
    """
    parent_pid = os.getppid()
    if parent_pid <= 1:
        _LOGGER.debug("parent-watchdog: no parent to watch (ppid=%d); skipping", parent_pid)
        return

    _LOGGER.info(
        "parent-watchdog: watching parent process PID %d (platform=%s)",
        parent_pid,
        sys.platform,
    )
    if sys.platform == "win32":
        await _watch_windows(parent_pid)
    else:
        await _watch_unix(parent_pid, poll_seconds=poll_seconds)
