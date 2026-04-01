"""aiohttp application factory and startup/shutdown lifecycle."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .boards import BOARD_CATALOG
from .dashboard import DASHBOARD
from .handlers import (
    automations,
    boards,
    components,
    config_sections,
    devices,
    events,
    misc,
    operations,
    section_config,
)
from .handlers.util import cors_middleware
from .settings import DashboardSettings

_LOGGER = logging.getLogger(__name__)


def _get_frontend_dir() -> Path | None:
    """Return the path to the built frontend, or None if unavailable."""
    try:
        from esphome_device_builder_frontend import where  # type: ignore[import-not-found]

        return Path(where())
    except ImportError:
        return None


def _register_frontend(app: web.Application, frontend_dir: Path) -> None:
    """Register static file routes for the built frontend."""
    assets_dir = frontend_dir / "assets"
    if assets_dir.is_dir():
        app.router.add_static("/assets", assets_dir)

    index_html = frontend_dir / "index.html"

    async def handle_index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(index_html)

    # Serve individual frontend files at root level
    for path in frontend_dir.iterdir():
        if path.is_file():
            rel = path.name
            if rel == "index.html":
                app.router.add_get("/", handle_index)
            else:
                app.router.add_static(f"/{rel}", path)

    _LOGGER.info("Serving frontend from %s", frontend_dir)


async def _on_startup(app: web.Application) -> None:
    settings: DashboardSettings = app["settings"]
    BOARD_CATALOG.load()
    DASHBOARD.settings = settings
    await DASHBOARD.async_setup()
    app["dashboard_task"] = asyncio.create_task(DASHBOARD.async_run())
    await DASHBOARD.entries.async_update_entries()
    _LOGGER.info("Device Builder backend ready — config dir: %s", settings.config_dir)


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("dashboard_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app(settings: DashboardSettings) -> web.Application:
    """Create the aiohttp application."""
    app = web.Application(middlewares=[cors_middleware])
    app["settings"] = settings

    # Register all API route tables
    for module in (
        devices,
        boards,
        components,
        automations,
        config_sections,
        section_config,
        operations,
        events,
        misc,
    ):
        app.router.add_routes(module.routes)

    # Serve the built frontend if available
    frontend_dir = _get_frontend_dir()
    if frontend_dir and frontend_dir.is_dir():
        _register_frontend(app, frontend_dir)
    else:
        _LOGGER.info(
            "Frontend package not installed — running in API-only mode. "
            "Install esphome-device-builder-frontend for the web UI."
        )

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


def run(settings: DashboardSettings) -> None:
    """Start the server synchronously (blocks until stopped)."""
    logging.basicConfig(level=logging.DEBUG if settings.verbose else logging.INFO)
    app = create_app(settings)
    web.run_app(app, host=settings.host, port=settings.port)
