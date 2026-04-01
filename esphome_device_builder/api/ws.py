"""Multiplexed WebSocket API handler.

Single /ws endpoint. Dispatches commands to handlers registered on DeviceBuilder.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import orjson
from aiohttp import WSMsgType, web
from esphome.const import __version__ as esphome_version

from ..constants import __version__
from ..models import (
    CommandMessage,
    ErrorCode,
    ErrorMessage,
    EventMessage,
    ResultMessage,
    ServerInfoMessage,
)

if TYPE_CHECKING:
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)


class WebSocketClient:
    """A single WebSocket client connection."""

    def __init__(self, ws: web.WebSocketResponse, device_builder: DeviceBuilder) -> None:
        self._ws = ws
        self.device_builder = device_builder
        self._tasks: set[asyncio.Task] = set()

    async def send(self, data: dict[str, Any]) -> None:
        """Send a JSON message."""
        try:
            await self._ws.send_bytes(orjson.dumps(data))
        except ConnectionResetError:
            pass

    async def send_result(self, message_id: str, result: Any = None) -> None:
        """Send a success result, serializing dataclass results automatically."""
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        msg = ResultMessage(message_id=message_id, result=result)
        await self.send(msg.to_dict())

    async def send_error(self, message_id: str, error_code: ErrorCode, details: str = "") -> None:
        """Send an error."""
        msg = ErrorMessage(message_id=message_id, error_code=error_code, details=details)
        await self.send(msg.to_dict())

    async def send_event(self, message_id: str, event: str, data: Any = None) -> None:
        """Send a streaming event."""
        msg = EventMessage(message_id=message_id, event=event, data=data)
        await self.send(msg.to_dict())

    async def _handle_command(self, raw: dict[str, Any]) -> None:
        """Parse and dispatch a command."""
        try:
            cmd = CommandMessage.from_dict(raw)
        except Exception:
            await self.send_error("", ErrorCode.INVALID_MESSAGE, "Invalid command format")
            return

        handler = self.device_builder.command_handlers.get(cmd.command)
        if handler is None:
            await self.send_error(
                cmd.message_id,
                ErrorCode.UNKNOWN_COMMAND,
                f"Unknown command: {cmd.command}",
            )
            return

        try:
            result = await handler(client=self, message_id=cmd.message_id, **cmd.args)
            if result is not None:
                await self.send_result(cmd.message_id, result)
        except Exception:
            _LOGGER.exception("Error handling command %s", cmd.command)
            await self.send_error(
                cmd.message_id,
                ErrorCode.INTERNAL_ERROR,
                f"Command failed: {cmd.command}",
            )

    def create_task(self, coro: Any) -> asyncio.Task:
        """Create a tracked task."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cleanup(self) -> None:
        """Cancel all pending tasks."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """Multiplexed WebSocket API endpoint."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    device_builder: DeviceBuilder = request.app["device_builder"]
    client = WebSocketClient(ws, device_builder)

    # Send server info on connect
    info = ServerInfoMessage(server_version=__version__, esphome_version=esphome_version)
    await client.send(info.to_dict())

    try:
        async for msg in ws:
            if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                try:
                    raw = orjson.loads(msg.data)
                except Exception:
                    await client.send_error("", ErrorCode.INVALID_MESSAGE, "Invalid JSON")
                    continue
                client.create_task(client._handle_command(raw))
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        await client.cleanup()
        _LOGGER.debug("WebSocket client disconnected")

    return ws


def create_ws_routes() -> web.RouteTableDef:
    """Create the WebSocket route table."""
    routes = web.RouteTableDef()

    @routes.get("/ws")
    async def ws_route(request: web.Request) -> web.WebSocketResponse:
        return await websocket_handler(request)

    return routes
