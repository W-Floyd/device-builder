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
import threading

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

    Worst-case lag between parent death and shutdown is
    ``poll_seconds`` (the kernel reparents synchronously but
    we only observe it on the next poll tick).

    Does not catch :exc:`asyncio.CancelledError` — it propagates
    out so a future caller that awaits the task directly can
    distinguish "cancelled" from "parent died and we
    shut down". The current consumer
    (:meth:`DeviceBuilder._background_tasks` + ``gather(...,
    return_exceptions=True)``) treats either outcome the same,
    but suppressing cancellation here would silently regress
    that distinction.
    """
    while True:
        await asyncio.sleep(poll_seconds)
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
_WIN_WAIT_OBJECT_0 = 0
_WIN_WAIT_TIMEOUT = 0x102
# ``WaitForSingleObject`` timeout per poll tick, in milliseconds.
# Same cadence as the Unix path's ``_POLL_SECONDS`` so the
# cross-platform contract — "shutdown within ~2 s of cancel
# or parent death" — holds on both. A finite tick is what
# distinguishes this fix from the previous ``INFINITE`` wait:
# without it the worker thread can't observe ``cancel_event``
# and would leak past dashboard shutdown.
_WIN_POLL_MS = 2000


def _wait_for_parent_handle_windows(parent_pid: int, cancel_event: threading.Event) -> bool:
    """Wait for the parent's process handle to be signalled OR for cancel.

    Polls :c:func:`WaitForSingleObject` with a ``_WIN_POLL_MS``
    timeout. Each iteration checks the kernel handle (parent
    exited → ``WAIT_OBJECT_0``) and, on timeout, peeks at
    *cancel_event* before looping again. The polling loop is
    what lets :func:`_watch_windows` propagate cancellation
    back to the worker thread — a previous
    ``WaitForSingleObject(handle, INFINITE)`` shape would have
    left the thread blocked in the kernel until the parent
    actually died (potentially past dashboard shutdown,
    hanging ``ThreadPoolExecutor.shutdown(wait=True)`` at
    interpreter exit).

    Returns:
    * ``True``  — parent process exited (handle signalled).
    * ``False`` — couldn't open the handle (permission /
      already gone), or *cancel_event* was set, or
      ``WaitForSingleObject`` returned an unexpected status
      (treated as "couldn't observe" rather than "parent
      died" so we don't race an immediate shutdown at
      startup).

    Synchronous on purpose — meant to run inside
    :func:`asyncio.to_thread` so the asyncio loop stays free.
    """
    import ctypes  # noqa: PLC0415 — Windows-only path, deferred to keep import cost off non-Windows

    # ``WinDLL`` is only exposed by ``ctypes`` on Windows; type checkers
    # running on Linux / macOS for CI see ``ctypes`` without it. The
    # ``# type: ignore`` keeps mypy quiet while leaving the runtime
    # behaviour identical (this function is only ever reached on win32
    # — :func:`watch_parent_and_exit_on_death` dispatches on
    # :data:`sys.platform`).
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_WIN_PROCESS_SYNCHRONIZE, False, parent_pid)
    if not handle:
        return False
    try:
        while not cancel_event.is_set():
            # ``kernel32`` calls return ``Any`` (untyped C bindings).
            result = int(kernel32.WaitForSingleObject(handle, _WIN_POLL_MS))
            if result == _WIN_WAIT_OBJECT_0:
                return True
            if result != _WIN_WAIT_TIMEOUT:
                # ``WAIT_FAILED`` / ``WAIT_ABANDONED`` / anything
                # else — bail rather than spin. Same rationale as
                # the ``OpenProcess`` failure path: prefer "can't
                # observe" over "parent died" so a kernel quirk
                # doesn't kick the dashboard offline at startup.
                return False
        return False
    finally:
        kernel32.CloseHandle(handle)


async def _watch_windows(parent_pid: int) -> None:
    """Block-in-thread on the parent's handle; trigger shutdown on exit.

    Drives :func:`_wait_for_parent_handle_windows` via
    :func:`asyncio.to_thread`. On :exc:`asyncio.CancelledError`
    (normal dashboard shutdown), signal the worker thread via
    *cancel_event* before re-raising so the thread exits within
    one ``_WIN_POLL_MS`` tick instead of leaking until the
    parent dies. Without that handshake the worker would still
    be parked in ``WaitForSingleObject`` when Python's
    interpreter-exit ``ThreadPoolExecutor.shutdown(wait=True)``
    runs, hanging the whole process at exit time.
    """
    cancel_event = threading.Event()
    try:
        parent_exited = await asyncio.to_thread(
            _wait_for_parent_handle_windows, parent_pid, cancel_event
        )
    except asyncio.CancelledError:
        # Tell the worker thread to break out of its polling
        # loop. The thread observes the flag on its next
        # iteration (up to ``_WIN_POLL_MS`` later) and exits
        # cleanly; we re-raise so the cancellation chains to
        # our caller the way the Unix path does.
        cancel_event.set()
        raise
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
