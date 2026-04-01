"""WebSocket handlers for streaming ESPHome CLI operations.

Each endpoint accepts a JSON ``spawn`` message and then streams
``{"event": "line", "data": "..."}`` messages until the process exits,
followed by ``{"event": "exit", "code": N}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import aiohttp
from aiohttp import web

from ..dashboard import DASHBOARD
from ..entries import DashboardEntries, entry_state_to_bool
from .util import get_settings

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()

# Path to the esphome CLI executable
_ESPHOME_CMD = [sys.executable, "-m", "esphome"]


def _esphome_command(*args: str) -> list[str]:
    return [*_ESPHOME_CMD, *args]


async def _stream_process(
    ws: web.WebSocketResponse,
    command: list[str],
) -> None:
    """Run *command* and stream stdout/stderr line-by-line over *ws*."""
    _LOGGER.info("Running: %s", " ".join(command))
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        close_fds=(os.name != "nt"),
    )

    assert proc.stdout is not None

    while True:
        try:
            line = await proc.stdout.readline()
        except (asyncio.CancelledError, ConnectionResetError):
            break
        if not line:
            break
        text = line.decode("utf-8", errors="replace")
        if not ws.closed:
            await ws.send_json({"event": "line", "data": text})

    await proc.wait()
    if not ws.closed:
        await ws.send_json({"event": "exit", "code": proc.returncode})


async def _handle_ws_command(request: web.Request, build_command_fn: Any) -> web.WebSocketResponse:
    """Common WebSocket handler: wait for spawn message, run command, stream output."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    settings = get_settings(request)
    spawned = False

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "spawn" and not spawned:
                spawned = True
                try:
                    command = build_command_fn(settings, data)
                except (KeyError, ValueError) as exc:
                    await ws.send_json({"event": "line", "data": f"Error: {exc}\n"})
                    await ws.send_json({"event": "exit", "code": 1})
                    break
                await _stream_process(ws, command)
                break  # process finished; close connection

        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            break

    return ws


# ---------------------------------------------------------------------------
# /compile
# ---------------------------------------------------------------------------


def _build_compile(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    cmd = _esphome_command("compile", config_file)
    if data.get("only_generate"):
        cmd = _esphome_command("compile", "--only-generate", config_file)
    return cmd


@routes.get("/compile")
async def ws_compile(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_compile)


# ---------------------------------------------------------------------------
# /upload
# ---------------------------------------------------------------------------


def _build_upload(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    port = data.get("port", "")
    cmd = _esphome_command("upload", config_file)
    if port:
        cmd = _esphome_command("upload", "--device", port, config_file)
    return cmd


@routes.get("/upload")
async def ws_upload(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_upload)


# ---------------------------------------------------------------------------
# /logs
# ---------------------------------------------------------------------------


def _build_logs(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    port = data.get("port", "")
    cmd = _esphome_command("logs", config_file)
    if port:
        cmd = _esphome_command("logs", "--device", port, config_file)
    return cmd


@routes.get("/logs")
async def ws_logs(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_logs)


# ---------------------------------------------------------------------------
# /validate
# ---------------------------------------------------------------------------


def _build_validate(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    return _esphome_command("config", config_file)


@routes.get("/validate")
async def ws_validate(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_validate)


# ---------------------------------------------------------------------------
# /clean
# ---------------------------------------------------------------------------


def _build_clean(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    if data.get("clean_build_dir", False):
        return _esphome_command("clean", config_file)
    return _esphome_command("clean-mqtt", config_file)


@routes.get("/clean")
async def ws_clean(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_clean)


# ---------------------------------------------------------------------------
# /rename
# ---------------------------------------------------------------------------


def _build_rename(settings, data: dict) -> list[str]:
    config_file = str(settings.rel_path(data["configuration"]))
    new_name: str = data["newName"]
    return _esphome_command("rename", config_file, new_name)


@routes.get("/rename")
async def ws_rename(request: web.Request) -> web.WebSocketResponse:
    return await _handle_ws_command(request, _build_rename)


# ---------------------------------------------------------------------------
# POST /update-all
# ---------------------------------------------------------------------------


@routes.post("/update-all")
async def update_all(request: web.Request) -> web.Response:
    """Trigger OTA update for all online devices (fire-and-forget)."""
    settings = get_settings(request)
    entries = DASHBOARD.entries.async_all()
    online = [e for e in entries if entry_state_to_bool(e.state)]

    async def _run_upload(filename: str) -> None:
        config_file = str(settings.rel_path(filename))
        cmd = _esphome_command("upload", config_file)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                close_fds=(os.name != "nt"),
            )
            await proc.wait()
        except Exception:
            _LOGGER.exception("update-all failed for %s", filename)

    for entry in online:
        asyncio.create_task(_run_upload(entry.filename))

    return web.json_response({"queued": len(online)})
