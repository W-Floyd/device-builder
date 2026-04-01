"""Device-related HTTP handlers."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from esphome import const
from esphome.storage_json import (
    StorageJSON,
    ext_storage_path,
)
from esphome.dashboard.util.text import friendly_name_slugify

from ..dashboard import DASHBOARD
from ..entries import entry_state_to_bool
from ..metadata import (
    get_board_id,
    get_device_metadata,
    remove_device_metadata,
    set_device_metadata,
)
from ..models import (
    ConfiguredDevice,
    AdoptableDevice,
    DevicesResponse,
    UpdateDeviceRequest,
    UpdateDeviceResponse,
    WizardResponse,
)
from .util import error_response, get_settings, json_response

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_to_configured_device(entry, config_dir: Path) -> ConfiguredDevice:
    board_id = get_board_id(config_dir, entry.filename)
    d = entry.to_dict(board_id=board_id)
    return ConfiguredDevice(**{k: d[k] for k in ConfiguredDevice.__dataclass_fields__})


def _import_to_adoptable(dashboard, discovered) -> AdoptableDevice:
    return AdoptableDevice(
        name=discovered.device_name,
        friendly_name=discovered.friendly_name or "",
        package_import_url=discovered.package_import_url,
        project_name=discovered.project_name,
        project_version=discovered.project_version,
        network=discovered.network,
        ignored=discovered.device_name in dashboard.ignored_devices,
    )


# ---------------------------------------------------------------------------
# GET /devices
# ---------------------------------------------------------------------------


@routes.get("/devices")
async def list_devices(request: web.Request) -> web.Response:
    dashboard = DASHBOARD
    settings = get_settings(request)
    await dashboard.entries.async_request_update_entries()
    entries = dashboard.entries.async_all()
    configured_names = {e.name for e in entries}

    configured = [_entry_to_configured_device(e, settings.config_dir) for e in entries]
    importable = [
        _import_to_adoptable(dashboard, d)
        for d in dashboard.import_result.values()
        if d.device_name not in configured_names
    ]

    return json_response(asdict(DevicesResponse(configured=configured, importable=importable)))


# ---------------------------------------------------------------------------
# GET /ping
# ---------------------------------------------------------------------------


@routes.get("/ping")
async def ping(request: web.Request) -> web.Response:
    dashboard = DASHBOARD
    dashboard.ping_request.set()
    if dashboard.settings.status_use_mqtt:
        dashboard.mqtt_ping_request.set()
    result = {
        entry.filename: entry_state_to_bool(entry.state)
        for entry in dashboard.entries.async_all()
    }
    return json_response(result)


# ---------------------------------------------------------------------------
# POST /wizard
# ---------------------------------------------------------------------------


@routes.post("/wizard")
async def wizard(request: web.Request) -> web.Response:
    settings = get_settings(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    name: str = body.get("name", "").strip()
    if not name:
        return error_response("name is required")

    config_type = body.get("type", "basic")
    platform = body.get("platform", "")
    board = body.get("board", "")
    ssid = body.get("ssid", "")
    psk = body.get("psk", "")
    password = body.get("password", "")
    file_content: str | None = body.get("file_content")
    board_id: str | None = body.get("board_id")

    filename = f"{name}.yaml"
    config_path = settings.rel_path(filename)

    if config_path.exists():
        return error_response("File already exists", status=409)

    loop = asyncio.get_running_loop()

    def _write() -> None:
        if config_type == "upload" and file_content:
            config_path.write_text(file_content, encoding="utf-8")
            return

        friendly = friendly_name_slugify(name)

        if config_type == "empty":
            yaml = (
                f"esphome:\n"
                f"  name: {name}\n"
                f"  friendly_name: {friendly}\n\n"
            )
        else:  # "basic"
            yaml = (
                f"esphome:\n"
                f"  name: {name}\n"
                f"  friendly_name: {friendly}\n\n"
                f"{platform}:\n"
                f"  board: {board}\n\n"
                f"# Enable logging\n"
                f"logger:\n\n"
                f"# Enable Home Assistant API\n"
                f"api:\n"
                f"  encryption:\n"
                f"    key: !secret api_encryption_key\n\n"
                f"ota:\n"
                f"  - platform: esphome\n"
                f"    password: {password}\n\n"
                f"wifi:\n"
                f"  ssid: {ssid}\n"
                f"  password: {psk}\n"
            )
        config_path.write_text(yaml, encoding="utf-8")

    await loop.run_in_executor(None, _write)

    if board_id:
        await loop.run_in_executor(
            None,
            lambda: set_device_metadata(settings.config_dir, filename, board_id=board_id),
        )

    await DASHBOARD.entries.async_request_update_entries()
    return json_response(asdict(WizardResponse(configuration=filename)), status=200)


# ---------------------------------------------------------------------------
# PUT /devices/{name}
# ---------------------------------------------------------------------------


@routes.put("/devices/{name}")
async def update_device(request: web.Request) -> web.Response:
    settings = get_settings(request)
    name = request.match_info["name"]
    filename = f"{name}.yaml"

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    req = UpdateDeviceRequest(
        friendly_name=body.get("friendly_name"),
        comment=body.get("comment"),
        board_id=body.get("board_id"),
    )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: set_device_metadata(
            settings.config_dir,
            filename,
            board_id=req.board_id,
            friendly_name=req.friendly_name,
            comment=req.comment,
        ),
    )

    meta = get_device_metadata(settings.config_dir, filename)
    return json_response(
        asdict(
            UpdateDeviceResponse(
                name=name,
                friendly_name=meta.get("friendly_name", name),
                comment=meta.get("comment"),
                board_id=meta.get("board_id"),
            )
        )
    )


# ---------------------------------------------------------------------------
# GET /edit  POST /edit
# ---------------------------------------------------------------------------


@routes.get("/edit")
async def edit_get(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")
    if not configuration.endswith((".yaml", ".yml")):
        return error_response("Invalid configuration filename", status=404)

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if configuration in getattr(const, "SECRETS_FILES", []):
            content = ""
        else:
            return error_response("Not found", status=404)

    return web.Response(text=content, content_type="application/yaml")


@routes.post("/edit")
async def edit_post(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")
    if not configuration.endswith((".yaml", ".yml")):
        return error_response("Invalid configuration filename", status=404)

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    content = await request.text()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_text, content, "utf-8")
    await DASHBOARD.entries.async_request_update_entries()
    return web.Response(status=200)


# ---------------------------------------------------------------------------
# POST /delete
# ---------------------------------------------------------------------------


@routes.post("/delete")
async def delete_device(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.rel_url.query.get("configuration", "")

    if not configuration:
        return error_response("configuration is required")

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    if not path.exists():
        return error_response("File not found", status=404)

    loop = asyncio.get_running_loop()

    def _delete_all() -> None:
        # Active config file
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Could not delete config file: {exc}") from exc

        # Any previously trashed copy
        trash_copy = settings.config_dir / ".trash" / configuration
        trash_copy.unlink(missing_ok=True)

        # Archived storage copy
        archived = settings.config_dir / ".archive" / f"{configuration}.json"
        archived.unlink(missing_ok=True)

        # ESPHome storage JSON
        try:
            ext_storage_path(configuration).unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("Could not remove storage file for %s", configuration)

        # Board/metadata entry
        try:
            remove_device_metadata(settings.config_dir, configuration)
        except Exception:
            _LOGGER.warning("Could not remove metadata for %s", configuration)

    try:
        await loop.run_in_executor(None, _delete_all)
    except Exception as exc:
        _LOGGER.exception("Failed to delete device %s", configuration)
        return error_response(str(exc), status=500)

    await DASHBOARD.entries.async_request_update_entries()
    return web.Response(status=200)


# ---------------------------------------------------------------------------
# POST /import
# ---------------------------------------------------------------------------


@routes.post("/import")
async def import_device(request: web.Request) -> web.Response:
    settings = get_settings(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    name = body.get("name", "").strip()
    project_name = body.get("project_name", "")
    package_import_url = body.get("package_import_url", "")
    friendly_name = body.get("friendly_name")
    encryption = body.get("encryption")

    if not name or not package_import_url:
        return error_response("name and package_import_url are required")

    try:
        from esphome.config_helpers import import_config

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            import_config,
            settings.rel_path(f"{name}.yaml"),
            name,
            friendly_name,
            project_name,
            package_import_url,
            const.CONF_WIFI,
            encryption,
        )
    except FileExistsError:
        return error_response("File already exists", status=409)
    except ValueError as exc:
        return error_response(str(exc), status=422)
    except Exception as exc:
        _LOGGER.exception("Error importing device")
        return error_response(str(exc), status=500)

    DASHBOARD.ping_request.set()
    await DASHBOARD.entries.async_request_update_entries()
    return json_response({"configuration": f"{name}.yaml"})


# ---------------------------------------------------------------------------
# POST /ignore-device
# ---------------------------------------------------------------------------


@routes.post("/ignore-device")
async def ignore_device(request: web.Request) -> web.Response:
    dashboard = DASHBOARD
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    device_name = body.get("name", "")
    ignore = body.get("ignore", True)

    discovered = next(
        (d for d in dashboard.import_result.values() if d.device_name == device_name),
        None,
    )
    if discovered is None:
        return error_response("Device not found", status=404)

    if ignore:
        dashboard.ignored_devices.add(device_name)
    else:
        dashboard.ignored_devices.discard(device_name)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, dashboard.save_ignored_devices)
    return web.Response(status=204)
