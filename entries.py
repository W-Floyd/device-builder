"""DashboardEntry and DashboardEntries — device config file management."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esphome import const, util
from esphome.enum import StrEnum
from esphome.storage_json import StorageJSON, ext_storage_path

if TYPE_CHECKING:
    from .dashboard import DashboardEvent, ESPHomeDashboard

_LOGGER = logging.getLogger(__name__)

DashboardCacheKeyType = tuple[int, int, float, int]


class ReachableState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DNS_FAILURE = "dns_failure"
    UNKNOWN = "unknown"


class EntryStateSource(StrEnum):
    MDNS = "mdns"
    PING = "ping"
    MQTT = "mqtt"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EntryState:
    reachable: ReachableState
    source: EntryStateSource


UNKNOWN_STATE = EntryState(ReachableState.UNKNOWN, EntryStateSource.UNKNOWN)

_BOOL_TO_REACHABLE = {
    True: ReachableState.ONLINE,
    False: ReachableState.OFFLINE,
    None: ReachableState.UNKNOWN,
}
_REACHABLE_TO_BOOL = {
    ReachableState.ONLINE: True,
    ReachableState.OFFLINE: False,
    ReachableState.DNS_FAILURE: False,
    ReachableState.UNKNOWN: None,
}


@lru_cache
def bool_to_entry_state(value: bool | None, source: EntryStateSource) -> EntryState:
    return EntryState(_BOOL_TO_REACHABLE[value], source)


def entry_state_to_bool(state: EntryState) -> bool | None:
    return _REACHABLE_TO_BOOL[state.reachable]


class DashboardEntry:
    """Represents a single ESPHome config file on disk."""

    __slots__ = (
        "path",
        "filename",
        "_storage_path",
        "cache_key",
        "storage",
        "state",
        "_to_dict_cache",
    )

    def __init__(self, path: Path, cache_key: DashboardCacheKeyType) -> None:
        self.path = path
        self.filename: str = path.name
        self._storage_path = ext_storage_path(self.filename)
        self.cache_key = cache_key
        self.storage: StorageJSON | None = None
        self.state = UNKNOWN_STATE
        self._to_dict_cache: dict[str, Any] | None = None

    def load_from_disk(self, cache_key: DashboardCacheKeyType | None = None) -> None:
        self.storage = StorageJSON.load(self._storage_path)
        self._to_dict_cache = None
        if cache_key:
            self.cache_key = cache_key

    def to_dict(self, board_id: str = "") -> dict[str, Any]:
        """Return a JSON-serialisable dict (without live ping state)."""
        if self._to_dict_cache is None:
            self._to_dict_cache = {
                "name": self.name,
                "friendly_name": self.friendly_name,
                "configuration": self.filename,
                "path": str(self.path),
                "comment": self.comment,
                "address": self.address or "",
                "web_port": self.web_port,
                "target_platform": self.target_platform or "UNKNOWN",
                "current_version": self.current_version,
                "deployed_version": self.deployed_version,
                "loaded_integrations": sorted(self.loaded_integrations),
                "board_id": board_id,
            }
        return self._to_dict_cache

    # ------------------------------------------------------------------
    # Properties backed by StorageJSON
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        if self.storage is None:
            return self.filename.removesuffix(".yml").removesuffix(".yaml")
        return self.storage.name

    @property
    def friendly_name(self) -> str:
        if self.storage is None:
            return self.name
        return self.storage.friendly_name

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
    def no_mdns(self) -> bool | None:
        return self.storage.no_mdns if self.storage else None

    @property
    def current_version(self) -> str:
        return const.__version__

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


class DashboardEntries:
    """Manages all DashboardEntry objects, watching disk for changes."""

    __slots__ = (
        "_dashboard",
        "_loop",
        "_config_dir",
        "_entries",
        "_name_to_entry",
        "_update_lock",
        "_loaded",
    )

    def __init__(self, dashboard: ESPHomeDashboard) -> None:
        self._dashboard = dashboard
        self._loop = asyncio.get_running_loop()
        self._config_dir = dashboard.settings.config_dir
        self._entries: dict[Path, DashboardEntry] = {}
        self._name_to_entry: dict[str, set[DashboardEntry]] = defaultdict(set)
        self._update_lock = asyncio.Lock()
        self._loaded = False

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def get(self, path: Path) -> DashboardEntry | None:
        return self._entries.get(path)

    def get_by_name(self, name: str) -> set[DashboardEntry]:
        return self._name_to_entry.get(name, set())

    def all(self) -> list[DashboardEntry]:
        """Thread-safe: callable from non-async context."""
        return asyncio.run_coroutine_threadsafe(self._async_all(), self._loop).result()

    def async_all(self) -> list[DashboardEntry]:
        """Must be called from the event loop."""
        return list(self._entries.values())

    async def _async_all(self) -> list[DashboardEntry]:
        return list(self._entries.values())

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def async_set_state(self, entry: DashboardEntry, state: EntryState) -> None:
        from .dashboard import DashboardEvent  # avoid circular at module level

        if entry.state == state:
            return
        entry.state = state
        self._dashboard.bus.fire(
            DashboardEvent.ENTRY_STATE_CHANGED, {"entry": entry, "state": state}
        )

    def async_set_state_if_online_or_source(
        self, entry: DashboardEntry, state: EntryState
    ) -> None:
        if (
            state.reachable is ReachableState.ONLINE
            and entry.state.reachable is not ReachableState.ONLINE
        ) or entry.state.source in (EntryStateSource.UNKNOWN, state.source):
            self.async_set_state(entry, state)

    # ------------------------------------------------------------------
    # Disk updates
    # ------------------------------------------------------------------

    async def async_request_update_entries(self) -> None:
        if self._update_lock.locked():
            return
        await self.async_update_entries()

    async def async_update_entries(self) -> None:
        async with self._update_lock:
            await self._do_update()

    async def _do_update(self) -> None:
        from .dashboard import DashboardEvent  # avoid circular

        path_to_cache_key = await self._loop.run_in_executor(
            None, self._get_path_to_cache_key
        )

        entries = self._entries
        name_to_entry = self._name_to_entry
        bus = self._dashboard.bus

        removed = {e for p, e in entries.items() if p not in path_to_cache_key}
        added: dict[DashboardEntry, DashboardCacheKeyType] = {}
        updated: dict[DashboardEntry, DashboardCacheKeyType] = {}
        original_names: dict[DashboardEntry, str] = {}

        for path, cache_key in path_to_cache_key.items():
            entry = entries.get(path)
            if entry is None:
                added[DashboardEntry(path, cache_key)] = cache_key
            elif entry.cache_key != cache_key:
                updated[entry] = cache_key
                original_names[entry] = entry.name

        if added or updated:
            to_load = {**{e: k for e, k in added.items()}, **updated}
            await self._loop.run_in_executor(None, self._load_entries, to_load)

        for entry in added:
            entries[entry.path] = entry
            name_to_entry[entry.name].add(entry)
            bus.fire(DashboardEvent.ENTRY_ADDED, {"entry": entry})

        for entry in removed:
            del entries[entry.path]
            name_to_entry[entry.name].discard(entry)
            bus.fire(DashboardEvent.ENTRY_REMOVED, {"entry": entry})

        for entry in updated:
            old_name = original_names[entry]
            if old_name != entry.name:
                name_to_entry[old_name].discard(entry)
                name_to_entry[entry.name].add(entry)
            bus.fire(DashboardEvent.ENTRY_UPDATED, {"entry": entry})

    def _load_entries(self, entries: dict[DashboardEntry, DashboardCacheKeyType]) -> None:
        for entry, cache_key in entries.items():
            entry.load_from_disk(cache_key)

    def _get_path_to_cache_key(self) -> dict[Path, DashboardCacheKeyType]:
        result: dict[Path, DashboardCacheKeyType] = {}
        for file in util.list_yaml_files([self._config_dir]):
            try:
                stat = ext_storage_path(file.name).stat()
            except OSError:
                try:
                    stat = file.stat()
                except OSError:
                    continue
            result[file] = (stat.st_ino, stat.st_dev, stat.st_mtime, stat.st_size)
        return result
