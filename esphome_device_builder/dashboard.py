"""Core dashboard state: event bus, ESPHomeDashboard singleton."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Any

from esphome.storage_json import ignored_devices_storage_path

from .entries import DashboardEntries
from .settings import DashboardSettings

_LOGGER = logging.getLogger(__name__)


class DashboardEvent(StrEnum):
    ENTRY_ADDED = "entry_added"
    ENTRY_REMOVED = "entry_removed"
    ENTRY_UPDATED = "entry_updated"
    ENTRY_STATE_CHANGED = "entry_state_changed"
    IMPORTABLE_DEVICE_ADDED = "importable_device_added"
    IMPORTABLE_DEVICE_REMOVED = "importable_device_removed"
    INITIAL_STATE = "initial_state"
    PING = "ping"
    PONG = "pong"
    REFRESH = "refresh"


@dataclass
class Event:
    event_type: DashboardEvent
    data: dict[str, Any]


class EventBus:
    """Simple synchronous event bus."""

    def __init__(self) -> None:
        self._listeners: dict[DashboardEvent, set[Callable[[Event], None]]] = {}

    def add_listener(
        self, event_type: DashboardEvent, listener: Callable[[Event], None]
    ) -> Callable[[], None]:
        self._listeners.setdefault(event_type, set()).add(listener)
        return partial(self._remove_listener, event_type, listener)

    def _remove_listener(
        self, event_type: DashboardEvent, listener: Callable[[Event], None]
    ) -> None:
        self._listeners.get(event_type, set()).discard(listener)

    def fire(self, event_type: DashboardEvent, data: dict[str, Any]) -> None:
        event = Event(event_type, data)
        for listener in list(self._listeners.get(event_type, set())):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception("Event listener raised an exception")


class ESPHomeDashboard:
    """Holds all shared dashboard state."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.settings = DashboardSettings()
        self.entries: DashboardEntries | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.import_result: dict[str, Any] = {}  # name -> DiscoveredImport
        self.ignored_devices: set[str] = set()
        self.ping_request: asyncio.Event | None = None
        self.mqtt_ping_request = threading.Event()
        self._background_tasks: set[asyncio.Task] = set()

    async def async_setup(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.ping_request = asyncio.Event()
        self.entries = DashboardEntries(self)
        await self.loop.run_in_executor(None, self._load_ignored_devices)

    def _load_ignored_devices(self) -> None:
        storage_path = ignored_devices_storage_path()
        try:
            with storage_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                self.ignored_devices = set(data.get("ignored_devices", []))
        except FileNotFoundError:
            pass

    def save_ignored_devices(self) -> None:
        storage_path = ignored_devices_storage_path()
        with storage_path.open("w", encoding="utf-8") as f:
            json.dump({"ignored_devices": sorted(self.ignored_devices)}, f, indent=2)

    async def async_run(self) -> None:
        """Run background polling loop (entry file-watcher and ping)."""
        try:
            while True:
                await asyncio.sleep(5)
                if self.entries:
                    await self.entries.async_request_update_entries()
                if self.ping_request:
                    self.ping_request.set()
        except asyncio.CancelledError:
            pass

    def create_background_task(self, coro: Any) -> asyncio.Task:
        task = self.loop.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task


# Module-level singleton, initialised by server startup.
DASHBOARD = ESPHomeDashboard()
