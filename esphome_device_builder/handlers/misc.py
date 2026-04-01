"""Miscellaneous utility endpoints: version, serial ports, secrets, info, downloads."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from esphome import const, platformio_api, yaml_util
from esphome.storage_json import StorageJSON, ext_storage_path
from esphome.util import get_serial_ports

from ..metadata import get_preferences, set_preferences
from ..models import DownloadItem, SerialPort, VersionResponse
from .util import error_response, get_settings, json_response

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


# ---------------------------------------------------------------------------
# GET /version
# ---------------------------------------------------------------------------


@routes.get("/version")
async def version(request: web.Request) -> web.Response:
    return json_response(asdict(VersionResponse(version=const.__version__)))


# ---------------------------------------------------------------------------
# GET /serial-ports
# ---------------------------------------------------------------------------


@routes.get("/serial-ports")
async def serial_ports(request: web.Request) -> web.Response:
    loop = asyncio.get_running_loop()
    ports = await loop.run_in_executor(None, get_serial_ports)
    result = []
    for p in ports:
        desc = p.description
        if p.path == "/dev/ttyAMA0":
            desc = "UART pins on GPIO header"
        result.append(asdict(SerialPort(port=p.path, desc=desc)))
    return json_response(result)


# ---------------------------------------------------------------------------
# GET /secret_keys
# ---------------------------------------------------------------------------


@routes.get("/secret_keys")
async def secret_keys(request: web.Request) -> web.Response:
    settings = get_settings(request)
    loop = asyncio.get_running_loop()

    def _read() -> list[str]:
        try:
            secrets_path = settings.rel_path("secrets.yaml")
            data = yaml_util.load_yaml(secrets_path)
            return sorted(data.keys()) if isinstance(data, dict) else []
        except Exception:
            return []

    keys = await loop.run_in_executor(None, _read)
    return json_response(keys)


# ---------------------------------------------------------------------------
# GET/PUT /preferences
# ---------------------------------------------------------------------------


@routes.get("/preferences")
async def preferences_get(request: web.Request) -> web.Response:
    settings = get_settings(request)
    loop = asyncio.get_running_loop()
    prefs = await loop.run_in_executor(
        None, get_preferences, settings.absolute_config_dir
    )
    return json_response(prefs)


@routes.put("/preferences")
async def preferences_put(request: web.Request) -> web.Response:
    settings = get_settings(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    loop = asyncio.get_running_loop()
    updated = await loop.run_in_executor(
        None, set_preferences, settings.absolute_config_dir, body
    )
    return json_response(updated)


# ---------------------------------------------------------------------------
# GET /info?configuration=
# ---------------------------------------------------------------------------


@routes.get("/info")
async def info(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")

    try:
        yaml_path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    storage_path = ext_storage_path(configuration)
    storage = StorageJSON.load(storage_path)
    if storage is None:
        return error_response("Not found — compile the device first", status=404)

    return web.Response(
        text=storage.to_json(),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /json-config?configuration=
# ---------------------------------------------------------------------------


@routes.get("/json-config")
async def json_config(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")

    try:
        yaml_path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    loop = asyncio.get_running_loop()

    def _load():
        try:
            return yaml_util.load_yaml(yaml_path)
        except Exception as exc:
            return {"error": str(exc)}

    data = await loop.run_in_executor(None, _load)
    return json_response(data)


# ---------------------------------------------------------------------------
# GET /downloads?configuration=
# ---------------------------------------------------------------------------


@routes.get("/downloads")
async def downloads(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")

    try:
        yaml_path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    storage = StorageJSON.load(ext_storage_path(configuration))
    if storage is None:
        return error_response("Not found — compile the device first", status=404)

    loop = asyncio.get_running_loop()

    def _get_downloads():
        platform = (storage.target_platform or "").lower()
        try:
            from esphome.components.esp32 import VARIANTS as ESP32_VARIANTS
            if platform.upper() in ESP32_VARIANTS:
                platform_ = "esp32"
            elif platform in ("rtl87xx", "bk72xx", "ln882x", "libretiny"):
                platform_ = "libretiny"
            else:
                platform_ = platform

            module = importlib.import_module(f"esphome.components.{platform_}")
            get_types = getattr(module, "get_download_types")
            return get_types(storage)
        except Exception as exc:
            _LOGGER.warning("Could not determine download types: %s", exc)
            return []

    items = await loop.run_in_executor(None, _get_downloads)
    return json_response(items)


# ---------------------------------------------------------------------------
# GET /download.bin?configuration=&file=
# ---------------------------------------------------------------------------


@routes.get("/download.bin")
async def download_bin(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")
    file_name = request.rel_url.query.get("file") or request.rel_url.query.get("type")
    compressed = request.rel_url.query.get("compressed", "0") == "1"

    if not file_name:
        return error_response("file parameter is required", status=400)

    try:
        settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    storage = StorageJSON.load(ext_storage_path(configuration))
    if storage is None:
        return error_response("Not found — compile the device first", status=404)

    if storage.firmware_bin_path is None:
        return error_response("No firmware binary available", status=404)

    base_dir = storage.firmware_bin_path.parent.resolve()
    path = (base_dir / file_name).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        return error_response("Forbidden", status=403)

    if not path.is_file():
        return error_response("File not found", status=404)

    loop = asyncio.get_running_loop()

    def _read():
        data = path.read_bytes()
        return gzip.compress(data, 9) if compressed else data

    data = await loop.run_in_executor(None, _read)
    download_name = storage.name + "-" + file_name
    if compressed:
        download_name += ".gz"

    return web.Response(
        body=data,
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "Cache-Control": "no-cache",
        },
    )
