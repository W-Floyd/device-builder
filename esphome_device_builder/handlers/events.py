"""WebSocket /events handler — real-time dashboard state updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from aiohttp import web

from ..dashboard import DASHBOARD, DashboardEvent, Event
from ..entries import entry_state_to_bool
from ..metadata import get_board_id
from .util import get_settings

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


class _EventsConnection:
    """Manages a single /events WebSocket connection."""

    def __init__(self, ws: web.WebSocketResponse, settings: Any) -> None:
        self._ws = ws
        self._settings = settings
        self._unlisten: list[Any] = []

    async def run(self) -> None:
        dashboard = DASHBOARD
        await dashboard.entries.async_request_update_entries()

        # Send initial state
        entries = dashboard.entries.async_all()
        await self._send(
            {
                "event": DashboardEvent.INITIAL_STATE,
                "data": {
                    "devices": [
                        {**e.to_dict(board_id=get_board_id(self._settings.config_dir, e.filename))}
                        for e in entries
                    ],
                    "ping": {e.filename: entry_state_to_bool(e.state) for e in entries},
                },
            }
        )

        # Subscribe to bus events
        bus = dashboard.bus
        self._unlisten = [
            bus.add_listener(DashboardEvent.ENTRY_STATE_CHANGED, self._on_state_changed),
            bus.add_listener(
                DashboardEvent.ENTRY_ADDED, self._make_entry_handler(DashboardEvent.ENTRY_ADDED)
            ),
            bus.add_listener(
                DashboardEvent.ENTRY_REMOVED, self._make_entry_handler(DashboardEvent.ENTRY_REMOVED)
            ),
            bus.add_listener(
                DashboardEvent.ENTRY_UPDATED, self._make_entry_handler(DashboardEvent.ENTRY_UPDATED)
            ),
            bus.add_listener(DashboardEvent.IMPORTABLE_DEVICE_ADDED, self._on_importable_added),
            bus.add_listener(DashboardEvent.IMPORTABLE_DEVICE_REMOVED, self._on_importable_removed),
        ]

        try:
            # Consume client messages (ping/refresh)
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_client_message(msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            for unlisten in self._unlisten:
                unlisten()

    async def _handle_client_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if data.get("type") == "ping":
            await self._send({"event": DashboardEvent.PONG})

    async def _send(self, message: dict) -> None:
        if self._ws.closed:
            return
        try:
            await self._ws.send_json(message)
        except (ConnectionResetError, RuntimeError):
            pass

    def _on_state_changed(self, event: Event) -> None:
        entry = event.data["entry"]
        state = event.data["state"]
        asyncio.get_event_loop().create_task(
            self._send(
                {
                    "event": DashboardEvent.ENTRY_STATE_CHANGED,
                    "data": {
                        "filename": entry.filename,
                        "name": entry.name,
                        "state": entry_state_to_bool(state),
                    },
                }
            )
        )

    def _make_entry_handler(self, event_type: DashboardEvent):
        def handler(event: Event) -> None:
            entry = event.data["entry"]
            board_id = get_board_id(self._settings.config_dir, entry.filename)
            asyncio.get_event_loop().create_task(
                self._send(
                    {
                        "event": event_type,
                        "data": entry.to_dict(board_id=board_id),
                    }
                )
            )

        return handler

    def _on_importable_added(self, event: Event) -> None:
        device = event.data.get("device") or event.data
        device_name = device.get("name") if isinstance(device, dict) else None
        if device_name and DASHBOARD.entries.get_by_name(device_name):
            return
        asyncio.get_event_loop().create_task(
            self._send({"event": DashboardEvent.IMPORTABLE_DEVICE_ADDED, "data": device})
        )

    def _on_importable_removed(self, event: Event) -> None:
        asyncio.get_event_loop().create_task(
            self._send({"event": DashboardEvent.IMPORTABLE_DEVICE_REMOVED, "data": event.data})
        )


@routes.get("/events")
async def ws_events(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    settings = get_settings(request)
    conn = _EventsConnection(ws, settings)
    await conn.run()
    return ws
