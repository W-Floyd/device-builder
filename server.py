"""aiohttp application factory and startup/shutdown lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

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


async def _on_startup(app: web.Application) -> None:
    settings: DashboardSettings = app["settings"]
    DASHBOARD.settings = settings
    await DASHBOARD.async_setup()
    app["dashboard_task"] = asyncio.create_task(DASHBOARD.async_run())
    await DASHBOARD.entries.async_update_entries()
    _LOGGER.info(
        "Device Builder backend ready — config dir: %s", settings.config_dir
    )


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("dashboard_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app(settings: DashboardSettings) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["settings"] = settings

    # Register all route tables
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

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


def run(settings: DashboardSettings) -> None:
    """Start the server synchronously (blocks until stopped)."""
    logging.basicConfig(level=logging.DEBUG if settings.verbose else logging.INFO)
    app = create_app(settings)
    web.run_app(app, host=settings.host, port=settings.port)
