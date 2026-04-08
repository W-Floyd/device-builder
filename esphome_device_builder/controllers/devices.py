"""Devices controller — device CRUD, file watching, CLI operations, state management."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esphome import const, util
from esphome.dashboard.util.text import friendly_name_slugify
from esphome.storage_json import StorageJSON, ext_storage_path, ignored_devices_storage_path

from ..helpers.api import api_command
from ..models import (
    AddComponentResponse,
    AdoptableDevice,
    ConfiguredDevice,
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

# Cache key: (inode, device, mtime, size)
_CacheKey = tuple[int, int, float, int]


# ---------------------------------------------------------------------------
# Device entry — wraps an ESPHome YAML config file on disk
# ---------------------------------------------------------------------------


class DeviceEntry:
    """A single ESPHome device config file with cached metadata."""

    __slots__ = ("_storage_path", "cache_key", "filename", "path", "storage")

    def __init__(self, path: Path, cache_key: _CacheKey) -> None:
        self.path = path
        self.filename = path.name
        self._storage_path = ext_storage_path(self.filename)
        self.cache_key = cache_key
        self.storage: StorageJSON | None = None

    def load_from_disk(self, cache_key: _CacheKey | None = None) -> None:
        """Load StorageJSON metadata from disk."""
        self.storage = StorageJSON.load(self._storage_path)
        if cache_key:
            self.cache_key = cache_key

    @property
    def name(self) -> str:
        if self.storage is None:
            return self.filename.removesuffix(".yml").removesuffix(".yaml")
        return self.storage.name

    @property
    def friendly_name(self) -> str:
        return self.storage.friendly_name if self.storage else self.name

    @property
    def comment(self) -> str | None:
        return self.storage.comment if self.storage else None

    @property
    def address(self) -> str | None:
        return self.storage.address if self.storage else None

    @property
    def web_port(self) -> int | None:
        return self.storage.web_port if self.storage else None

    @property
    def target_platform(self) -> str | None:
        return self.storage.target_platform if self.storage else None

    @property
    def deployed_version(self) -> str:
        if self.storage is None:
            return ""
        return self.storage.esphome_version or ""

    @property
    def loaded_integrations(self) -> list[str]:
        if self.storage is None:
            return []
        return list(self.storage.loaded_integrations)

    def to_configured_device(self, board_id: str = "") -> ConfiguredDevice:
        """Convert to a ConfiguredDevice model."""
        return ConfiguredDevice(
            name=self.name,
            friendly_name=self.friendly_name,
            configuration=self.filename,
            path=str(self.path),
            comment=self.comment,
            address=self.address or "",
            web_port=self.web_port,
            target_platform=self.target_platform or "UNKNOWN",
            current_version=const.__version__,
            deployed_version=self.deployed_version,
            loaded_integrations=sorted(self.loaded_integrations),
            board_id=board_id,
        )

    def to_legacy_dict(self, board_id: str = "") -> dict[str, Any]:
        """Format for legacy HA /devices endpoint."""
        return {
            "name": self.name,
            "friendly_name": self.friendly_name,
            "configuration": self.filename,
            "path": str(self.path),
            "comment": self.comment,
            "address": self.address or "",
            "web_port": self.web_port,
            "target_platform": self.target_platform or "UNKNOWN",
            "current_version": const.__version__,
            "deployed_version": self.deployed_version,
            "loaded_integrations": sorted(self.loaded_integrations),
            "board_id": board_id,
        }


# ---------------------------------------------------------------------------
# Devices controller
# ---------------------------------------------------------------------------


class DevicesController:
    """Manages device configurations, file watching, and CLI operations."""

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder
        self._entries: dict[Path, DeviceEntry] = {}
        self._name_to_entry: dict[str, set[DeviceEntry]] = defaultdict(set)
        self._update_lock = asyncio.Lock()

        # Device state
        self.import_result: dict[str, Any] = {}
        self.ignored_devices: set[str] = set()
        self.ping_request: asyncio.Event | None = None
        self.mqtt_ping_request = threading.Event()

    async def start(self) -> None:
        """Initialize the devices controller — load state, scan files."""
        self.ping_request = asyncio.Event()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_ignored_devices)
        await self.update_entries()

    async def poll(self) -> None:
        """Poll for file changes and device state."""
        await self._request_update_entries()
        if self.ping_request:
            self.ping_request.set()

    # ------------------------------------------------------------------
    # File watching
    # ------------------------------------------------------------------

    def get_all_entries(self) -> list[DeviceEntry]:
        """Get all device entries."""
        return list(self._entries.values())

    async def _request_update_entries(self) -> None:
        if self._update_lock.locked():
            return
        await self.update_entries()

    async def update_entries(self) -> None:
        """Scan disk for YAML file changes."""
        async with self._update_lock:
            await self._do_update()

    async def _do_update(self) -> None:
        loop = asyncio.get_running_loop()
        path_to_cache_key = await loop.run_in_executor(None, self._get_path_to_cache_key)

        entries = self._entries
        name_to_entry = self._name_to_entry
        bus = self._db.bus

        removed = {e for p, e in entries.items() if p not in path_to_cache_key}
        added: dict[DeviceEntry, _CacheKey] = {}
        updated: dict[DeviceEntry, _CacheKey] = {}

        for path, cache_key in path_to_cache_key.items():
            entry = entries.get(path)
            if entry is None:
                added[DeviceEntry(path, cache_key)] = cache_key
            elif entry.cache_key != cache_key:
                updated[entry] = cache_key

        if added or updated:
            to_load = {**dict(added), **updated}
            await loop.run_in_executor(None, self._load_entries, to_load)

        for entry in added:
            entries[entry.path] = entry
            name_to_entry[entry.name].add(entry)
            bus.fire(EventType.ENTRY_ADDED, {"entry": entry})

        for entry in removed:
            del entries[entry.path]
            name_to_entry[entry.name].discard(entry)
            bus.fire(EventType.ENTRY_REMOVED, {"entry": entry})

        for entry in updated:
            bus.fire(EventType.ENTRY_UPDATED, {"entry": entry})

    def _load_entries(self, entries: dict[DeviceEntry, _CacheKey]) -> None:
        for entry, cache_key in entries.items():
            entry.load_from_disk(cache_key)

    def _get_path_to_cache_key(self) -> dict[Path, _CacheKey]:
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
        await self._request_update_entries()
        config_dir = self._db.settings.config_dir
        configured_names: set[str] = set()

        configured = []
        for entry in self.get_all_entries():
            configured_names.add(entry.name)
            board_id_val = get_board_id(config_dir, entry.filename)
            configured.append(entry.to_configured_device(board_id_val))

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
        # For now return empty — state tracking will be added later
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
            await loop.run_in_executor(
                None,
                lambda: set_device_metadata(
                    self._db.settings.config_dir, filename, board_id=board_id
                ),
            )

        await self._request_update_entries()
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
        await self._request_update_entries()

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
        await self._request_update_entries()

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
        from ..yaml_editor import generate_component_yaml

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
        await self._request_update_entries()

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
        await self._request_update_entries()
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
