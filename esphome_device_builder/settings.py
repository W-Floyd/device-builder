"""Dashboard settings."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path

from esphome.core import CORE
from esphome.helpers import get_bool_env

_DASHBOARD_SENTINEL_FILE = "___DASHBOARD_SENTINEL___.yaml"


def _hash_password(password: str) -> bytes:
    import hashlib

    return hashlib.sha256(password.encode("utf-8")).digest()


@dataclass
class DashboardSettings:
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
        # Set sentinel so CORE.config_path.parent == config_dir
        CORE.config_path = self.config_dir / _DASHBOARD_SENTINEL_FILE

    def rel_path(self, *parts: str) -> Path:
        """Return a path relative to the config dir, validated against path traversal."""
        joined = self.config_dir.joinpath(*parts)
        joined.resolve().relative_to(self.absolute_config_dir)  # raises ValueError if outside
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
        if not self.using_auth:
            return True
        username_ok = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        password_ok = hmac.compare_digest(self.password_hash, _hash_password(password))
        return username_ok and password_ok
