"""Devices controller — device CRUD, CLI operations, state management."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any

from esphome import const
from esphome.dashboard.util.text import friendly_name_slugify
from esphome.storage_json import ext_storage_path

from ..entries import entry_state_to_bool
from ..helpers.api import api_command
from ..models import (
    AddComponentResponse,
    AdoptableDevice,
    ConfiguredDevice,
    DevicesResponse,
    UpdateDeviceResponse,
    WizardResponse,
)
from .config import (
    get_board_id,
    get_device_metadata,
    remove_device_metadata,
    set_device_metadata,
)

try:
    from esphome.config_helpers import import_config
except ImportError:
    import_config = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from ..api.ws import WebSocketClient
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)
_ESPHOME_CMD = [sys.executable, "-m", "esphome"]


class DevicesController:
    """Manages device configurations, metadata, and CLI operations."""

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder

    # ------------------------------------------------------------------
    # Device listing
    # ------------------------------------------------------------------

    @api_command("devices/list")
    async def list_devices(self, **kwargs: Any) -> DevicesResponse:
        """List all configured and importable devices."""
        db = self._db
        await db.entries.async_request_update_entries()
        entries = db.entries.async_all()
        config_dir = db.settings.config_dir
        configured_names = {e.name for e in entries}

        configured = []
        for entry in entries:
            board_id_val = get_board_id(config_dir, entry.filename)
            d = entry.to_dict(board_id=board_id_val)
            configured.append(
                ConfiguredDevice(**{k: d[k] for k in ConfiguredDevice.__dataclass_fields__})
            )

        importable = []
        for discovered in db.import_result.values():
            if discovered.device_name in configured_names:
                continue
            importable.append(
                AdoptableDevice(
                    name=discovered.device_name,
                    friendly_name=discovered.friendly_name or "",
                    package_import_url=discovered.package_import_url,
                    project_name=discovered.project_name,
                    project_version=discovered.project_version,
                    network=discovered.network,
                    ignored=discovered.device_name in db.ignored_devices,
                )
            )

        return DevicesResponse(configured=configured, importable=importable)

    @api_command("devices/get_states")
    async def get_device_states(self, **kwargs: Any) -> dict:
        """Get online/offline state for all devices."""
        db = self._db
        db.ping_request.set()
        if db.settings.status_use_mqtt:
            db.mqtt_ping_request.set()
        return {
            entry.filename: entry_state_to_bool(entry.state) for entry in db.entries.async_all()
        }

    # ------------------------------------------------------------------
    # Device CRUD
    # ------------------------------------------------------------------

    @api_command("devices/create")
    async def create_device(
        self,
        *,
        name: str,
        config_type: str = "basic",
        platform: str = "",
        board: str = "",
        ssid: str = "",
        psk: str = "",
        password: str = "",
        file_content: str | None = None,
        board_id: str | None = None,
        **kwargs: Any,
    ) -> WizardResponse:
        """Create a new device configuration."""
        db = self._db
        name = name.strip()
        if not name:
            msg = "name is required"
            raise ValueError(msg)

        filename = f"{name}.yaml"
        config_path = db.settings.rel_path(filename)

        if config_path.exists():
            msg = "File already exists"
            raise FileExistsError(msg)

        loop = asyncio.get_running_loop()

        def _write() -> None:
            if config_type == "upload" and file_content:
                config_path.write_text(file_content, encoding="utf-8")
                return

            friendly = friendly_name_slugify(name)
            if config_type == "empty":
                yaml = f"esphome:\n  name: {name}\n  friendly_name: {friendly}\n\n"
            else:
                yaml = (
                    f"esphome:\n"
                    f"  name: {name}\n"
                    f"  friendly_name: {friendly}\n\n"
                    f"{platform}:\n"
                    f"  board: {board}\n\n"
                    f"logger:\n\n"
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
                lambda: set_device_metadata(db.settings.config_dir, filename, board_id=board_id),
            )

        await db.entries.async_request_update_entries()
        return WizardResponse(configuration=filename)

    @api_command("devices/update")
    async def update_device(
        self,
        *,
        name: str,
        friendly_name: str | None = None,
        comment: str | None = None,
        board_id: str | None = None,
        **kwargs: Any,
    ) -> UpdateDeviceResponse:
        """Update device metadata."""
        db = self._db
        filename = f"{name}.yaml"
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            None,
            lambda: set_device_metadata(
                db.settings.config_dir,
                filename,
                board_id=board_id,
                friendly_name=friendly_name,
                comment=comment,
            ),
        )

        meta = get_device_metadata(db.settings.config_dir, filename)
        return UpdateDeviceResponse(
            name=name,
            friendly_name=meta.get("friendly_name", name),
            comment=meta.get("comment"),
            board_id=meta.get("board_id"),
        )

    @api_command("devices/delete")
    async def delete_device(self, *, configuration: str, **kwargs: Any) -> None:
        """Delete a device and all associated files."""
        db = self._db
        config_path = db.settings.rel_path(configuration)

        if not config_path.exists():
            msg = "File not found"
            raise FileNotFoundError(msg)

        loop = asyncio.get_running_loop()

        def _delete_all() -> None:
            config_path.unlink(missing_ok=True)
            (db.settings.config_dir / ".trash" / configuration).unlink(missing_ok=True)
            (db.settings.config_dir / ".archive" / f"{configuration}.json").unlink(missing_ok=True)
            try:
                ext_storage_path(configuration).unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning("Could not remove storage file for %s", configuration)
            try:
                remove_device_metadata(db.settings.config_dir, configuration)
            except Exception:
                _LOGGER.warning("Could not remove metadata for %s", configuration)

        await loop.run_in_executor(None, _delete_all)
        await db.entries.async_request_update_entries()

    @api_command("devices/get_config")
    async def get_config(self, *, configuration: str, **kwargs: Any) -> str:
        """Read device config YAML."""
        db = self._db
        path = db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, path.read_text, "utf-8")

    @api_command("devices/update_config")
    async def update_config(self, *, configuration: str, content: str, **kwargs: Any) -> None:
        """Write device config YAML."""
        db = self._db
        path = db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, path.write_text, content, "utf-8")
        await db.entries.async_request_update_entries()

    @api_command("devices/add_component")
    async def add_component(
        self,
        *,
        configuration: str,
        component_id: str,
        fields: dict[str, Any] | None = None,
        sub_entities: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AddComponentResponse:
        """Add a component to a device configuration.

        Looks up the component from the catalog, validates required fields,
        generates YAML, and appends to the device config file.
        """
        from ..yaml_editor import generate_component_yaml

        db = self._db
        component = db.components.get_component(component_id=component_id)
        if component is None:
            msg = f"Unknown component: {component_id}"
            raise ValueError(msg)

        fields = fields or {}

        # Validate required fields
        for entry in component.config_entries:
            if entry.required and entry.key not in fields:
                msg = f"Missing required field: {entry.key}"
                raise ValueError(msg)

        # Generate YAML block
        yaml_block = generate_component_yaml(component, fields, sub_entities)

        # Read existing config and append
        config_path = db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        existing = await loop.run_in_executor(None, config_path.read_text, "utf-8")
        new_yaml = existing.rstrip() + "\n\n" + yaml_block + "\n"
        await loop.run_in_executor(None, config_path.write_text, new_yaml, "utf-8")
        await db.entries.async_request_update_entries()

        return AddComponentResponse(yaml=new_yaml)

    @api_command("devices/import")
    async def import_device(
        self,
        *,
        name: str,
        project_name: str = "",
        package_import_url: str = "",
        friendly_name: str | None = None,
        encryption: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """Import/adopt a discovered device."""
        if import_config is None:
            msg = "import_config not available in this ESPHome version"
            raise RuntimeError(msg)

        db = self._db
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            import_config,
            db.settings.rel_path(f"{name}.yaml"),
            name,
            friendly_name,
            project_name,
            package_import_url,
            const.CONF_WIFI,
            encryption,
        )

        db.ping_request.set()
        await db.entries.async_request_update_entries()
        return {"configuration": f"{name}.yaml"}

    @api_command("devices/ignore")
    async def toggle_ignore(self, *, name: str, ignore: bool = True, **kwargs: Any) -> None:
        """Mark a device as ignored/visible in the import list."""
        db = self._db
        if ignore:
            db.ignored_devices.add(name)
        else:
            db.ignored_devices.discard(name)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, db.save_ignored_devices)

    # ------------------------------------------------------------------
    # CLI operations (compile, upload, logs, validate, clean)
    # ------------------------------------------------------------------

    async def _stream_esphome_command(
        self,
        client: WebSocketClient,
        message_id: str,
        command: str,
        config_path: str,
        extra_args: list[str] | None = None,
    ) -> None:
        """Run an esphome CLI command and stream output to the WebSocket client."""
        cmd = [*_ESPHOME_CMD, command, config_path]
        if extra_args:
            cmd.extend(extra_args)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        assert proc.stdout is not None
        async for line_bytes in proc.stdout:
            line = line_bytes.decode("utf-8", errors="replace")
            await client.send_event(message_id, "output", line)

        exit_code = await proc.wait()
        await client.send_event(
            message_id, "result", {"success": exit_code == 0, "code": exit_code}
        )

    @api_command("devices/compile")
    async def compile_device(
        self, *, configuration: str, client: Any = None, message_id: str = "", **kwargs: Any
    ) -> None:
        """Compile a device configuration."""
        config_path = str(self._db.settings.rel_path(configuration))
        await self._stream_esphome_command(client, message_id, "compile", config_path)

    @api_command("devices/upload")
    async def upload_device(
        self,
        *,
        configuration: str,
        port: str = "",
        client: Any = None,
        message_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Upload firmware to a device."""
        config_path = str(self._db.settings.rel_path(configuration))
        extra = ["--device", port] if port else []
        await self._stream_esphome_command(client, message_id, "upload", config_path, extra)

    @api_command("devices/logs")
    async def stream_logs(
        self,
        *,
        configuration: str,
        port: str = "",
        client: Any = None,
        message_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Stream device logs."""
        config_path = str(self._db.settings.rel_path(configuration))
        extra = ["--device", port] if port else []
        await self._stream_esphome_command(client, message_id, "logs", config_path, extra)

    @api_command("devices/validate")
    async def validate_device(
        self, *, configuration: str, client: Any = None, message_id: str = "", **kwargs: Any
    ) -> None:
        """Validate a device configuration."""
        config_path = str(self._db.settings.rel_path(configuration))
        await self._stream_esphome_command(client, message_id, "config", config_path)

    @api_command("devices/clean")
    async def clean_device(
        self, *, configuration: str, client: Any = None, message_id: str = "", **kwargs: Any
    ) -> None:
        """Clean build files for a device."""
        config_path = str(self._db.settings.rel_path(configuration))
        await self._stream_esphome_command(client, message_id, "clean", config_path)
