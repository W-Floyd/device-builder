"""Devices controller — device CRUD, file watching, CLI operations, state management."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esphome import const, util
from esphome.zeroconf import AsyncEsphomeZeroconf

try:
    from icmplib import async_ping as icmp_ping
except ImportError:
    icmp_ping = None  # type: ignore[assignment]
from esphome.dashboard.util.text import friendly_name_slugify
from esphome.storage_json import StorageJSON, ext_storage_path, ignored_devices_storage_path

from ..helpers.api import api_command
from ..helpers.yaml import generate_component_yaml
from ..models import (
    AddComponentResponse,
    AdoptableDevice,
    Device,
    DevicesResponse,
    DeviceState,
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
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)
_ESPHOME_CMD: list[str] = []  # resolved in start()

# Cache key for file change detection: (inode, device, mtime, size)
_CacheKey = tuple[int, int, float, int]
_ESPHOME_SERVICE_TYPE = "_esphomelib._tcp.local."
_PING_INTERVAL = 60  # seconds between ping sweeps
_PING_BATCH_SIZE = 10


def _generate_device_yaml(
    name: str,
    friendly_name: str,
    board: Any,
    ssid: str,
    psk: str,
) -> str:
    """Generate a complete device YAML config from a board definition.

    Produces the base config with platform settings, logging, API, OTA,
    and WiFi — the most common/sane defaults for a new device.
    """
    esphome_cfg = board.esphome
    lines: list[str] = []

    # Board reference comment
    board_label = board.name
    if board.manufacturer:
        board_label = f"{board.name} ({board.manufacturer})"
    lines.append(f"# Board: {board_label}")
    lines.append(f"# Definition: definitions/boards/{board.id}/manifest.yaml")
    lines.append("")

    # ESPHome core
    lines.append("esphome:")
    lines.append(f"  name: {name}")
    lines.append(f"  friendly_name: {friendly_name}")
    lines.append("")

    # Platform config — only the parameters ESPHome needs, no PlatformIO board ID
    platform = esphome_cfg.platform
    hardware = board.hardware
    lines.append(f"{platform}:")
    if esphome_cfg.variant:
        lines.append(f"  variant: {esphome_cfg.variant}")
    if hardware.flash_size:
        lines.append(f"  flash_size: {hardware.flash_size}")
    if esphome_cfg.framework:
        lines.append("  framework:")
        lines.append(f"    type: {esphome_cfg.framework}")
    lines.append("")

    # Logging
    lines.append("logger:")
    lines.append("")

    # Home Assistant API — unique encryption key per device
    api_key = base64.b64encode(secrets.token_bytes(32)).decode()
    lines.append("api:")
    lines.append("  encryption:")
    lines.append(f'    key: "{api_key}"')
    lines.append("")

    # OTA
    lines.append("ota:")
    lines.append("  - platform: esphome")
    lines.append("")

    # WiFi (only for boards that support it)
    connectivity = [c.value for c in board.hardware.connectivity] if board.hardware else []
    has_wifi = "wifi" in connectivity or not connectivity  # assume wifi if no connectivity data
    if has_wifi:
        lines.append("wifi:")
        if ssid:
            lines.append(f"  ssid: {ssid}")
            lines.append(f"  password: {psk}")
        else:
            lines.append("  ssid: !secret wifi_ssid")
            lines.append("  password: !secret wifi_password")
        lines.append("")

    return "\n".join(lines)


def _load_device_from_storage(path: Path, board_id: str = "") -> Device:
    """Build a Device model from a YAML config file and its StorageJSON."""
    filename = path.name
    storage = StorageJSON.load(ext_storage_path(filename))
    name = storage.name if storage else filename.removesuffix(".yml").removesuffix(".yaml")

    # Detect pending changes: YAML modified after last compile
    has_pending: bool | None = None  # None = never compiled
    if storage and storage.firmware_bin_path and storage.firmware_bin_path.exists():
        yaml_mtime = path.stat().st_mtime
        bin_mtime = storage.firmware_bin_path.stat().st_mtime
        has_pending = yaml_mtime > bin_mtime

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
        has_pending_changes=has_pending,
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

        # Device connectivity state
        self._state_source: dict[str, str] = {}  # device name → "mdns" | "ping"

        # Discovery state
        self.import_result: dict[str, Any] = {}
        self.ignored_devices: set[str] = set()

        # mDNS
        self._zeroconf: AsyncEsphomeZeroconf | None = None
        self._mdns_browser: Any = None

    async def start(self) -> None:
        """Initialize — load state, scan files, start discovery."""
        global _ESPHOME_CMD
        from .firmware import _find_esphome_cmd

        _ESPHOME_CMD = _find_esphome_cmd()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_ignored_devices)
        await self.scan_devices()
        _LOGGER.info("Devices controller started — %d devices loaded", len(self._devices))

        # Start mDNS browser for device discovery
        await self._start_mdns_browser()

        # Start ping sweep as fallback
        self._db.create_background_task(self._ping_loop())

    async def poll(self) -> None:
        """Poll for file changes."""
        await self._request_scan()

    # ------------------------------------------------------------------
    # Device connectivity state
    # ------------------------------------------------------------------

    def _find_device_by_name(self, name: str) -> Device | None:
        """Find a loaded device by its ESPHome name."""
        for device in self._devices.values():
            if device.name == name:
                return device
        return None

    def _set_device_state(self, name: str, state: DeviceState, source: str) -> None:
        """Update a device's connectivity state with source priority.

        mDNS always wins. Ping only sets state when mDNS hasn't resolved it.
        Fires DEVICE_STATE_CHANGED event if state actually changes.
        """
        device = self._find_device_by_name(name)
        if device is None:
            return
        # mDNS always wins — ping cannot override mDNS
        current_source = self._state_source.get(name, "unknown")
        if source == "ping" and current_source == "mdns":
            return
        if device.state == state:
            return
        device.state = state
        self._state_source[name] = source
        _LOGGER.debug("Device %s: %s (via %s)", name, state, source)
        self._db.bus.fire(EventType.DEVICE_STATE_CHANGED, {"device": device})

    # ------------------------------------------------------------------
    # mDNS browser
    # ------------------------------------------------------------------

    async def _start_mdns_browser(self) -> None:
        """Start the mDNS browser for ESPHome device discovery."""
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser

            self._zeroconf = AsyncEsphomeZeroconf()

            def _on_service_state_change(
                zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange
            ) -> None:
                # Extract device name from mDNS name (e.g. "my-device._esphomelib._tcp.local.")
                device_name = name.split(".")[0].replace("-", "_")

                if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                    self._set_device_state(device_name, DeviceState.ONLINE, "mdns")
                elif state_change == ServiceStateChange.Removed:
                    self._set_device_state(device_name, DeviceState.OFFLINE, "mdns")
                    # Clear the mdns source so ping can take over
                    self._state_source.pop(device_name, None)

            self._mdns_browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                _ESPHOME_SERVICE_TYPE,
                handlers=[_on_service_state_change],
            )
            _LOGGER.info("mDNS browser started for %s", _ESPHOME_SERVICE_TYPE)
        except Exception:
            _LOGGER.warning("Could not start mDNS browser — device discovery limited to ping")

    # ------------------------------------------------------------------
    # Ping sweep (fallback)
    # ------------------------------------------------------------------

    async def _ping_loop(self) -> None:
        """Periodically ping devices not already discovered by mDNS."""
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                await self._ping_sweep()
        except asyncio.CancelledError:
            pass

    async def _ping_sweep(self) -> None:
        """Ping all devices not already marked online by mDNS."""
        if icmp_ping is None:
            return

        devices_to_ping = [
            d
            for d in self._devices.values()
            if d.address and self._state_source.get(d.name, "unknown") != "mdns"
        ]

        if not devices_to_ping:
            return

        _LOGGER.debug("Pinging %d devices", len(devices_to_ping))

        # Ping in batches
        for i in range(0, len(devices_to_ping), _PING_BATCH_SIZE):
            batch = devices_to_ping[i : i + _PING_BATCH_SIZE]
            tasks = [self._ping_device(d) for d in batch]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _ping_device(self, device: Device) -> None:
        """Ping a single device and update state."""
        try:
            result = await icmp_ping(device.address, count=1, timeout=3, privileged=False)
            if result.is_alive:
                self._set_device_state(device.name, DeviceState.ONLINE, "ping")
            else:
                self._set_device_state(device.name, DeviceState.OFFLINE, "ping")
        except Exception:  # noqa: S110
            # Ping failed (permissions, network error) — don't change state
            pass

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
        """Get connectivity state for all devices."""
        return {d.configuration: d.state.value for d in self._devices.values()}

    # ------------------------------------------------------------------
    # API commands — device CRUD
    # ------------------------------------------------------------------

    @api_command("devices/create")
    async def create_device(
        self,
        *,
        name: str,
        board_id: str,
        config_type: str = "basic",
        ssid: str = "",
        psk: str = "",
        file_content: str | None = None,
        **kwargs: Any,
    ) -> WizardResponse:
        """Create a new device configuration.

        Looks up the board definition to generate proper ESPHome platform
        config with sane defaults. The board_id is stored in metadata for
        future reference but does NOT appear in the device YAML — ESPHome
        only cares about platform/variant/board settings.
        """
        name = name.strip()
        if not name:
            msg = "name is required"
            raise ValueError(msg)

        filename = f"{name}.yaml"
        config_path = self._db.settings.rel_path(filename)

        if config_path.exists():
            msg = "File already exists"
            raise FileExistsError(msg)

        # Look up board definition
        board = None
        if self._db.boards:
            board = await self._db.boards.get_board(board_id=board_id)
        if board is None:
            msg = f"Unknown board: {board_id}"
            raise ValueError(msg)

        loop = asyncio.get_running_loop()

        def _write() -> None:
            if config_type == "upload" and file_content:
                config_path.write_text(file_content, encoding="utf-8")
                return

            friendly = friendly_name_slugify(name)

            if config_type == "empty":
                yaml = f"esphome:\n  name: {name}\n  friendly_name: {friendly}\n\n"
                config_path.write_text(yaml, encoding="utf-8")
                return

            yaml = _generate_device_yaml(name, friendly, board, ssid, psk)
            config_path.write_text(yaml, encoding="utf-8")

        await loop.run_in_executor(None, _write)

        # Store board_id in metadata for future reference
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

    async def _delete_single(self, configuration: str) -> None:
        """Delete a single device and all associated files."""
        config_path = self._db.settings.rel_path(configuration)
        if not config_path.exists():
            msg = f"File not found: {configuration}"
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

    @api_command("devices/delete")
    async def delete_device(self, *, configuration: str, **kwargs: Any) -> None:
        """Delete a device and all associated files."""
        await self._delete_single(configuration)
        await self._request_scan()

    @api_command("devices/delete_bulk")
    async def delete_bulk(
        self, *, configurations: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Delete multiple devices at once.

        Returns a result per device: {configuration, success, error?}.
        """
        results: list[dict[str, Any]] = []
        for configuration in configurations:
            try:
                await self._delete_single(configuration)
                results.append({"configuration": configuration, "success": True})
            except Exception as exc:
                results.append(
                    {
                        "configuration": configuration,
                        "success": False,
                        "error": str(exc),
                    }
                )
        await self._request_scan()
        return results

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
        component = await self._db.components.get_component(component_id=component_id)
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
    # YAML validation and live log streaming (per-connection, not queued)
    # ------------------------------------------------------------------

    @api_command("devices/validate")
    async def validate_config(
        self,
        *,
        configuration: str,
        client: Any = None,
        message_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Validate a device YAML config. Streams output per-connection."""
        config_path = str(self._db.settings.rel_path(configuration))
        cmd = [*_ESPHOME_CMD, "config", config_path]

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
        """Stream live device logs. Per-connection, not queued."""
        config_path = str(self._db.settings.rel_path(configuration))
        cmd = [*_ESPHOME_CMD, "logs", config_path]
        if port:
            cmd.extend(["--device", port])

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
