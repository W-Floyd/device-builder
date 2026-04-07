"""ESPHome Device Builder — core application singleton.

The DeviceBuilder class is the main entry point. It owns all controllers,
the event bus, the file watcher, and the aiohttp web application.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .helpers.api import CommandHandler, collect_api_commands
from .helpers.json import cors_middleware
from .settings import DashboardSettings

if TYPE_CHECKING:
    from .entries import DashboardEntries

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


class DashboardEvent(StrEnum):
    """Events fired by the dashboard."""

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
    """A dashboard event."""

    event_type: DashboardEvent
    data: dict[str, Any]


class EventBus:
    """Simple synchronous event bus."""

    def __init__(self) -> None:
        self._listeners: dict[DashboardEvent, set[Callable[[Event], None]]] = {}

    def add_listener(
        self, event_type: DashboardEvent, listener: Callable[[Event], None]
    ) -> Callable[[], None]:
        """Add a listener. Returns an unsubscribe callback."""
        self._listeners.setdefault(event_type, set()).add(listener)
        return partial(self._remove_listener, event_type, listener)

    def _remove_listener(
        self, event_type: DashboardEvent, listener: Callable[[Event], None]
    ) -> None:
        self._listeners.get(event_type, set()).discard(listener)

    def fire(self, event_type: DashboardEvent, data: dict[str, Any]) -> None:
        """Fire an event to all listeners."""
        event = Event(event_type, data)
        for listener in list(self._listeners.get(event_type, set())):
            try:
                listener(event)
            except Exception:
                _LOGGER.exception("Event listener raised an exception")


# ---------------------------------------------------------------------------
# DeviceBuilder
# ---------------------------------------------------------------------------


class DeviceBuilder:
    """Core application singleton.

    Owns all controllers, the event bus, the file watcher, and the web app.
    """

    def __init__(self, settings: DashboardSettings) -> None:
        """Initialize the Device Builder."""
        self.settings = settings
        self.bus = EventBus()
        self.loop: asyncio.AbstractEventLoop | None = None

        # State
        self.import_result: dict[str, Any] = {}
        self.ignored_devices: set[str] = set()
        self.ping_request: asyncio.Event | None = None
        self.mqtt_ping_request = threading.Event()

        # Controllers — populated in start()
        self.entries: DashboardEntries | None = None
        self.boards: Any = None
        self.components: Any = None
        self.devices: Any = None
        self.config: Any = None
        self.metadata_ctrl: Any = None

        # Command registry — populated from controllers
        self.command_handlers: dict[str, CommandHandler] = {}

        # Background tasks
        self._background_tasks: set[asyncio.Task] = set()
        self._bg_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the application — load catalogs, initialize controllers."""
        from .controllers.boards import BoardCatalog
        from .controllers.components import ComponentCatalog
        from .controllers.config import ConfigController
        from .controllers.devices import DevicesController
        from .entries import DashboardEntries

        self.loop = asyncio.get_running_loop()
        self.ping_request = asyncio.Event()

        # Initialize controllers
        self.boards = BoardCatalog()
        self.boards.load()
        self.components = ComponentCatalog()
        self.components.load()
        self.entries = DashboardEntries(self)
        self.config = ConfigController(self)
        self.devices = DevicesController(self)

        # Load ignored devices
        await self.loop.run_in_executor(None, self._load_ignored_devices)

        # Collect command handlers from all controllers
        for controller in (self.boards, self.components, self.config, self.devices):
            self.command_handlers.update(collect_api_commands(controller))

        # Register built-in commands
        self._register_builtin_commands()

        # Initial file scan
        await self.entries.async_update_entries()

        # Start background polling
        self._bg_task = asyncio.create_task(self._run_background())

        _LOGGER.info(
            "Device Builder ready — config dir: %s, %d commands registered",
            self.settings.config_dir,
            len(self.command_handlers),
        )

    async def stop(self) -> None:
        """Shut down the application."""
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    async def _run_background(self) -> None:
        """Background polling loop (file watcher, ping)."""
        try:
            while True:
                await asyncio.sleep(5)
                if self.entries:
                    await self.entries.async_request_update_entries()
                if self.ping_request:
                    self.ping_request.set()
        except asyncio.CancelledError:
            pass

    def _load_ignored_devices(self) -> None:
        """Load ignored devices list from disk."""
        from esphome.storage_json import ignored_devices_storage_path

        storage_path = ignored_devices_storage_path()
        try:
            with storage_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                self.ignored_devices = set(data.get("ignored_devices", []))
        except FileNotFoundError:
            pass

    def save_ignored_devices(self) -> None:
        """Persist ignored devices list to disk."""
        from esphome.storage_json import ignored_devices_storage_path

        storage_path = ignored_devices_storage_path()
        with storage_path.open("w", encoding="utf-8") as f:
            json.dump({"ignored_devices": sorted(self.ignored_devices)}, f, indent=2)

    def create_background_task(self, coro: Any) -> asyncio.Task:
        """Create a tracked background task."""
        task = self.loop.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _register_builtin_commands(self) -> None:
        """Register built-in commands that don't belong to a specific controller."""
        from .helpers.api import api_command

        @api_command("ping")
        async def cmd_ping(**kwargs: Any) -> dict:
            return {"pong": True}

        self.command_handlers["ping"] = cmd_ping

    # ------------------------------------------------------------------
    # Web application
    # ------------------------------------------------------------------

    def create_app(self) -> web.Application:
        """Create the aiohttp application."""
        app = web.Application(middlewares=[cors_middleware])
        app["device_builder"] = self

        # WebSocket API
        from .api.ws import create_ws_routes

        app.router.add_routes(create_ws_routes())

        # Legacy REST endpoints (HA backward compat)
        from .api.legacy import create_legacy_routes

        app.router.add_routes(create_legacy_routes())

        # Frontend serving
        frontend_dir = self._get_frontend_dir()
        if frontend_dir and frontend_dir.is_dir():
            self._register_frontend(app, frontend_dir)
        else:
            _LOGGER.info(
                "Frontend package not installed — running in API-only mode. "
                "Install esphome-device-builder-frontend for the web UI."
            )

        # Lifecycle hooks
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)

        return app

    async def _on_startup(self, app: web.Application) -> None:
        await self.start()

    async def _on_cleanup(self, app: web.Application) -> None:
        await self.stop()

    def run(self) -> None:
        """Start the HTTP server (blocking)."""
        logging.basicConfig(level=logging.DEBUG if self.settings.verbose else logging.INFO)
        app = self.create_app()
        web.run_app(app, host=self.settings.host, port=self.settings.port)

    @staticmethod
    def _get_frontend_dir() -> Path | None:
        """Return the path to the built frontend, or None if unavailable."""
        try:
            from esphome_device_builder_frontend import where  # type: ignore[import-not-found]

            return Path(where())
        except ImportError:
            return None

    @staticmethod
    def _register_frontend(app: web.Application, frontend_dir: Path) -> None:
        """Register static file routes for the built frontend."""
        assets_dir = frontend_dir / "assets"
        if assets_dir.is_dir():
            app.router.add_static("/assets", assets_dir)

        index_html = frontend_dir / "index.html"

        async def handle_index(request: web.Request) -> web.FileResponse:
            return web.FileResponse(index_html)

        for path in frontend_dir.iterdir():
            if path.is_file():
                rel = path.name
                if rel == "index.html":
                    app.router.add_get("/", handle_index)
                else:
                    app.router.add_static(f"/{rel}", path)

        _LOGGER.info("Serving frontend from %s", frontend_dir)
