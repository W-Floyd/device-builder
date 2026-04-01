"""Devices controller — device CRUD, file watching, CLI operations, state management."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esphome import const, util
from esphome.dashboard.util.text import friendly_name_slugify
from esphome.storage_json import StorageJSON, ext_storage_path, ignored_devices_storage_path

from ..helpers.api import api_command
from ..helpers.yaml import generate_component_yaml
from ..models import (
    AddComponentResponse,
    AdoptableDevice,
    Device,
    DevicesResponse,
    EventType,
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

# Cache key for file change detection: (inode, device, mtime, size)
_CacheKey = tuple[int, int, float, int]


def _load_device_from_storage(path: Path, board_id: str = "") -> Device:
    """Build a Device model from a YAML config file and its StorageJSON."""
    filename = path.name
    storage = StorageJSON.load(ext_storage_path(filename))
    name = storage.name if storage else filename.removesuffix(".yml").removesuffix(".yaml")
    return Device(
        name=name,
        friendly_name=storage.friendly_name if storage else name,
        configuration=filename,
        path=str(path),
        comment=storage.comment if storage else None,
        address=storage.address or "" if storage else "",
        web_port=storage.web_port if storage else None,
        target_platform=storage.target_platform or "UNKNOWN" if storage else "UNKNOWN",
        current_version=const.__version__,
        deployed_version=storage.esphome_version or "" if storage else "",
        loaded_integrations=sorted(storage.loaded_integrations) if storage else [],
        board_id=board_id,
    )


# ---------------------------------------------------------------------------
# Devices controller
# ---------------------------------------------------------------------------


class DevicesController:
    """Manage device configurations, file watching, and CLI operations."""

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder
        self._devices: dict[Path, Device] = {}
        self._cache_keys: dict[Path, _CacheKey] = {}
        self._scan_lock = asyncio.Lock()

        # Device state
        self.import_result: dict[str, Any] = {}
        self.ignored_devices: set[str] = set()
        self.ping_request: asyncio.Event | None = None
        self.mqtt_ping_request = threading.Event()

    async def start(self) -> None:
        """Initialize — load state, scan files."""
        self.ping_request = asyncio.Event()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_ignored_devices)
        await self.scan_devices()

    async def poll(self) -> None:
        """Poll for file changes and device state."""
        await self._request_scan()
        if self.ping_request:
            self.ping_request.set()

    # ------------------------------------------------------------------
    # File scanning
    # ------------------------------------------------------------------

    def get_devices(self) -> list[Device]:
        """Get all loaded devices."""
        return list(self._devices.values())

    async def _request_scan(self) -> None:
        if self._scan_lock.locked():
            return
        await self.scan_devices()

    async def scan_devices(self) -> None:
        """Scan the config folder for YAML file changes."""
        async with self._scan_lock:
            await self._do_scan()

    async def _do_scan(self) -> None:
        loop = asyncio.get_running_loop()
        config_dir = self._db.settings.config_dir
        bus = self._db.bus

        # Get current state of files on disk
        path_to_cache_key = await loop.run_in_executor(None, self._get_path_to_cache_key)

        old_paths = set(self._devices.keys())
        new_paths = set(path_to_cache_key.keys())

        removed_paths = old_paths - new_paths
        added_paths = new_paths - old_paths
        possibly_updated = old_paths & new_paths

        # Detect updated files (cache key changed)
        updated_paths = {
            p for p in possibly_updated if path_to_cache_key[p] != self._cache_keys.get(p)
        }

        # Load new and updated devices from disk
        paths_to_load = added_paths | updated_paths
        if paths_to_load:
            devices_loaded = await loop.run_in_executor(
                None, self._load_devices, paths_to_load, config_dir
            )
            for path, device in devices_loaded.items():
                self._devices[path] = device
                self._cache_keys[path] = path_to_cache_key[path]
                event = EventType.DEVICE_ADDED if path in added_paths else EventType.DEVICE_UPDATED
                bus.fire(event, {"device": device})

        # Remove deleted devices
        for path in removed_paths:
            device = self._devices.pop(path, None)
            self._cache_keys.pop(path, None)
            if device:
                bus.fire(EventType.DEVICE_REMOVED, {"device": device})

    def _load_devices(self, paths: set[Path], config_dir: Path) -> dict[Path, Device]:
        """Load Device models from disk (runs in executor)."""
        result: dict[Path, Device] = {}
        for path in paths:
            try:
                board_id = get_board_id(config_dir, path.name)
                result[path] = _load_device_from_storage(path, board_id)
            except Exception:
                _LOGGER.warning("Failed to load device from %s", path.name)
        return result

    def _get_path_to_cache_key(self) -> dict[Path, _CacheKey]:
        """Scan disk for YAML files and build cache keys."""
        result: dict[Path, _CacheKey] = {}
        for file in util.list_yaml_files([self._db.settings.config_dir]):
            try:
                stat = ext_storage_path(file.name).stat()
            except OSError:
                try:
                    stat = file.stat()
                except OSError:
                    continue
            result[file] = (stat.st_ino, stat.st_dev, stat.st_mtime, stat.st_size)
        return result

    # ------------------------------------------------------------------
    # Ignored devices
    # ------------------------------------------------------------------

    def _load_ignored_devices(self) -> None:
        storage_path = ignored_devices_storage_path()
        try:
            with storage_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                self.ignored_devices = set(data.get("ignored_devices", []))
        except FileNotFoundError:
            pass

    def _save_ignored_devices(self) -> None:
        storage_path = ignored_devices_storage_path()
        with storage_path.open("w", encoding="utf-8") as f:
            json.dump({"ignored_devices": sorted(self.ignored_devices)}, f, indent=2)

    # ------------------------------------------------------------------
    # API commands — device listing
    # ------------------------------------------------------------------

    @api_command("devices/list")
    async def list_devices(self, **kwargs: Any) -> DevicesResponse:
        """List all configured and importable devices."""
        await self._request_scan()
        configured = self.get_devices()
        configured_names = {d.name for d in configured}

        importable = []
        for discovered in self.import_result.values():
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
                    ignored=discovered.device_name in self.ignored_devices,
                )
            )

        return DevicesResponse(configured=configured, importable=importable)

    @api_command("devices/get_states")
    async def get_device_states(self, **kwargs: Any) -> dict:
        """Get online/offline state for all devices."""
        if self.ping_request:
            self.ping_request.set()
        if self._db.settings.status_use_mqtt:
            self.mqtt_ping_request.set()
        return {}

    # ------------------------------------------------------------------
    # API commands — device CRUD
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
        name = name.strip()
        if not name:
            msg = "name is required"
            raise ValueError(msg)

        filename = f"{name}.yaml"
        config_path = self._db.settings.rel_path(filename)

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
            config_dir = self._db.settings.config_dir
            await loop.run_in_executor(
                None, lambda: set_device_metadata(config_dir, filename, board_id=board_id)
            )

        await self._request_scan()
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
        filename = f"{name}.yaml"
        loop = asyncio.get_running_loop()
        config_dir = self._db.settings.config_dir

        await loop.run_in_executor(
            None,
            lambda: set_device_metadata(
                config_dir,
                filename,
                board_id=board_id,
                friendly_name=friendly_name,
                comment=comment,
            ),
        )

        meta = get_device_metadata(config_dir, filename)
        return UpdateDeviceResponse(
            name=name,
            friendly_name=meta.get("friendly_name", name),
            comment=meta.get("comment"),
            board_id=meta.get("board_id"),
        )

    @api_command("devices/rename")
    async def rename_device(
        self,
        *,
        configuration: str,
        new_name: str,
        **kwargs: Any,
    ) -> None:
        """Rename a device via esphome CLI."""
        config_path = str(self._db.settings.rel_path(configuration))
        cmd = [*_ESPHOME_CMD, "rename", config_path, new_name]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
        )
        # ESPHome rename prompts for confirmation — send 'y'
        if proc.stdin:
            proc.stdin.write(b"y\n")
            await proc.stdin.drain()
            proc.stdin.close()
        await proc.wait()
        await self._request_scan()

    @api_command("devices/delete")
    async def delete_device(self, *, configuration: str, **kwargs: Any) -> None:
        """Delete a device and all associated files."""
        config_path = self._db.settings.rel_path(configuration)
        if not config_path.exists():
            msg = "File not found"
            raise FileNotFoundError(msg)

        loop = asyncio.get_running_loop()
        config_dir = self._db.settings.config_dir

        def _delete_all() -> None:
            config_path.unlink(missing_ok=True)
            (config_dir / ".trash" / configuration).unlink(missing_ok=True)
            (config_dir / ".archive" / f"{configuration}.json").unlink(missing_ok=True)
            try:
                ext_storage_path(configuration).unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning("Could not remove storage file for %s", configuration)
            try:
                remove_device_metadata(config_dir, configuration)
            except Exception:
                _LOGGER.warning("Could not remove metadata for %s", configuration)

        await loop.run_in_executor(None, _delete_all)
        await self._request_scan()

    @api_command("devices/get_config")
    async def get_config(self, *, configuration: str, **kwargs: Any) -> str:
        """Read device config YAML."""
        path = self._db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, path.read_text, "utf-8")

    @api_command("devices/update_config")
    async def update_config(self, *, configuration: str, content: str, **kwargs: Any) -> None:
        """Write device config YAML."""
        path = self._db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, path.write_text, content, "utf-8")
        await self._request_scan()

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
        """Add a component to a device configuration."""
        component = self._db.components.get_component(component_id=component_id)
        if component is None:
            msg = f"Unknown component: {component_id}"
            raise ValueError(msg)

        fields = fields or {}
        for entry in component.config_entries:
            if entry.required and entry.key not in fields:
                msg = f"Missing required field: {entry.key}"
                raise ValueError(msg)

        yaml_block = generate_component_yaml(component, fields, sub_entities)

        config_path = self._db.settings.rel_path(configuration)
        loop = asyncio.get_running_loop()
        existing = await loop.run_in_executor(None, config_path.read_text, "utf-8")
        new_yaml = existing.rstrip() + "\n\n" + yaml_block + "\n"
        await loop.run_in_executor(None, config_path.write_text, new_yaml, "utf-8")
        await self._request_scan()

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

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            import_config,
            self._db.settings.rel_path(f"{name}.yaml"),
            name,
            friendly_name,
            project_name,
            package_import_url,
            const.CONF_WIFI,
            encryption,
        )

        if self.ping_request:
            self.ping_request.set()
        await self._request_scan()
        return {"configuration": f"{name}.yaml"}

    @api_command("devices/ignore")
    async def toggle_ignore(self, *, name: str, ignore: bool = True, **kwargs: Any) -> None:
        """Mark a device as ignored/visible in the import list."""
        if ignore:
            self.ignored_devices.add(name)
        else:
            self.ignored_devices.discard(name)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._save_ignored_devices)

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
        """Run an esphome CLI command and stream output."""
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
