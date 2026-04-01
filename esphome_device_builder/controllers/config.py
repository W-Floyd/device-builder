"""Config controller — settings, preferences, secrets, version, serial ports."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esphome import yaml_util
from esphome.const import __version__ as esphome_version
from esphome.core import CORE
from esphome.helpers import get_bool_env
from esphome.storage_json import StorageJSON, ext_storage_path
from esphome.util import get_serial_ports

from ..constants import __version__ as server_version
from ..helpers.api import api_command

if TYPE_CHECKING:
    from ..device_builder import DeviceBuilder

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_SENTINEL_FILE = "___DASHBOARD_SENTINEL___.yaml"
_METADATA_FILE = ".device-builder.json"
_PREFS_KEY = "_preferences"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()


@dataclass
class DashboardSettings:
    """Application settings parsed from CLI args and environment."""

    config_dir: Path = field(default_factory=Path)
    absolute_config_dir: Path | None = None
    username: str = ""
    password_hash: bytes = field(default_factory=bytes)
    using_password: bool = False
    on_ha_addon: bool = False
    cookie_secret: str | None = None
    verbose: bool = False
    port: int = 6052
    host: str = "0.0.0.0"

    def parse_args(self, args: object) -> None:
        """Parse CLI arguments into settings."""
        self.on_ha_addon = getattr(args, "ha_addon", False)
        password = getattr(args, "password", None) or os.getenv("PASSWORD") or ""
        if not self.on_ha_addon:
            self.username = getattr(args, "username", None) or os.getenv("USERNAME") or ""
            self.using_password = bool(password)
        if self.using_password:
            self.password_hash = _hash_password(password)
        self.config_dir = Path(args.configuration)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.absolute_config_dir = self.config_dir.resolve()
        self.verbose = getattr(args, "verbose", False)
        self.port = getattr(args, "port", 6052)
        self.host = getattr(args, "host", "0.0.0.0")
        CORE.config_path = self.config_dir / _DASHBOARD_SENTINEL_FILE

    def rel_path(self, *parts: str) -> Path:
        """Return a path relative to the config dir, validated against path traversal."""
        joined = self.config_dir.joinpath(*parts)
        joined.resolve().relative_to(self.absolute_config_dir)
        return joined

    @property
    def status_use_mqtt(self) -> bool:
        return get_bool_env("ESPHOME_DASHBOARD_USE_MQTT")

    @property
    def using_ha_addon_auth(self) -> bool:
        if not self.on_ha_addon:
            return False
        return not get_bool_env("DISABLE_HA_AUTHENTICATION")

    @property
    def using_auth(self) -> bool:
        return self.using_password or self.using_ha_addon_auth

    def check_password(self, username: str, password: str) -> bool:
        """Verify username and password."""
        if not self.using_auth:
            return True
        username_ok = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        password_ok = hmac.compare_digest(self.password_hash, _hash_password(password))
        return username_ok and password_ok


# ---------------------------------------------------------------------------
# Metadata persistence (device-builder.json)
# ---------------------------------------------------------------------------


def _load_metadata(config_dir: Path) -> dict[str, Any]:
    path = config_dir / _METADATA_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_metadata(config_dir: Path, data: dict[str, Any]) -> None:
    path = config_dir / _METADATA_FILE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_board_id(config_dir: Path, filename: str) -> str:
    """Get the board_id for a device."""
    return _load_metadata(config_dir).get(filename, {}).get("board_id", "")


def set_device_metadata(
    config_dir: Path,
    filename: str,
    *,
    board_id: str | None = None,
    friendly_name: str | None = None,
    comment: str | None = None,
) -> None:
    """Set metadata fields for a device."""
    data = _load_metadata(config_dir)
    entry = data.setdefault(filename, {})
    if board_id is not None:
        entry["board_id"] = board_id
    if friendly_name is not None:
        entry["friendly_name"] = friendly_name
    if comment is not None:
        entry["comment"] = comment
    _save_metadata(config_dir, data)


def get_device_metadata(config_dir: Path, filename: str) -> dict[str, Any]:
    """Get all metadata for a device."""
    return _load_metadata(config_dir).get(filename, {})


def remove_device_metadata(config_dir: Path, filename: str) -> None:
    """Remove metadata for a device."""
    data = _load_metadata(config_dir)
    data.pop(filename, None)
    _save_metadata(config_dir, data)


def get_preferences(config_dir: Path) -> dict[str, Any]:
    """Get user preferences."""
    return _load_metadata(config_dir).get(_PREFS_KEY, {})


def set_preferences(config_dir: Path, prefs: dict[str, Any]) -> dict[str, Any]:
    """Update user preferences."""
    data = _load_metadata(config_dir)
    current = data.setdefault(_PREFS_KEY, {})
    current.update(prefs)
    _save_metadata(config_dir, data)
    return current


# ---------------------------------------------------------------------------
# ConfigController
# ---------------------------------------------------------------------------


class ConfigController:
    """Manages application configuration, preferences, and system info."""

    def __init__(self, device_builder: DeviceBuilder) -> None:
        self._db = device_builder

    @api_command("config/version")
    async def get_version(self, **kwargs: Any) -> dict:
        """Get ESPHome and server version."""
        return {"server_version": server_version, "esphome_version": esphome_version}

    @api_command("config/serial_ports")
    async def get_serial_ports_cmd(self, **kwargs: Any) -> list[dict]:
        """List available serial ports."""
        loop = asyncio.get_running_loop()
        ports = await loop.run_in_executor(None, get_serial_ports)
        return [
            {"port": p.path, "desc": p.description if p.description != "n/a" else p.path}
            for p in ports
        ]

    @api_command("config/get_preferences")
    async def get_prefs(self, **kwargs: Any) -> dict:
        """Get user preferences."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, get_preferences, self._db.settings.config_dir)

    @api_command("config/set_preferences")
    async def set_prefs(self, **kwargs: Any) -> dict:
        """Update user preferences."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, set_preferences, self._db.settings.config_dir, kwargs
        )

    @api_command("config/get_secrets")
    async def get_secrets(self, **kwargs: Any) -> list[str]:
        """Get secret key names from secrets.yaml."""
        loop = asyncio.get_running_loop()

        def _read_secrets() -> list[str]:
            secrets_path = self._db.settings.config_dir / "secrets.yaml"
            if not secrets_path.exists():
                return []
            try:
                data = yaml_util.load_yaml(str(secrets_path))
                return sorted(data.keys()) if isinstance(data, dict) else []
            except Exception:
                return []

        return await loop.run_in_executor(None, _read_secrets)

    @api_command("config/get_info")
    async def get_info(self, *, configuration: str, **kwargs: Any) -> dict | None:
        """Get compiled device metadata (StorageJSON) for a configuration."""
        loop = asyncio.get_running_loop()

        try:
            self._db.settings.rel_path(configuration)
        except ValueError:
            return None

        def _load_info() -> dict | None:
            storage = StorageJSON.load(ext_storage_path(configuration))
            if storage is None:
                return None
            return {
                "name": storage.name,
                "friendly_name": storage.friendly_name,
                "comment": storage.comment,
                "address": storage.address,
                "web_port": storage.web_port,
                "target_platform": storage.target_platform,
                "current_version": storage.esphome_version,
                "deployed_version": storage.firmware_bin_path,
                "loaded_integrations": storage.loaded_integrations,
            }

        return await loop.run_in_executor(None, _load_info)
