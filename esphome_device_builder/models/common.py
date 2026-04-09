"""Common/shared data models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mashumaro.mixins.orjson import DataClassORJSONMixin

# ---------------------------------------------------------------------------
# Paged response base
# ---------------------------------------------------------------------------


@dataclass
class PagedResponse(DataClassORJSONMixin):
    """Base for paginated API responses."""

    total: int = 0
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Config entry (shared by component config and section config)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    """Events pushed to connected clients via subscribe_events."""

    # Device config file changes (detected by disk scanner)
    DEVICE_ADDED = "device_added"
    DEVICE_REMOVED = "device_removed"
    DEVICE_UPDATED = "device_updated"

    # Device online/offline state changes
    DEVICE_STATE_CHANGED = "device_state_changed"

    # Discoverable device changes
    IMPORTABLE_DEVICE_ADDED = "importable_device_added"
    IMPORTABLE_DEVICE_REMOVED = "importable_device_removed"


# ---------------------------------------------------------------------------
# Config entry types
# ---------------------------------------------------------------------------


class ConfigEntryType(StrEnum):
    """Config entry field types."""

    STRING = "string"
    SECURE_STRING = "secure_string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    SELECT = "select"
    PIN = "pin"
    TIME_PERIOD = "time_period"
    ICON = "icon"
    ID = "id"
    TRIGGER = "trigger"
    LABEL = "label"
    DIVIDER = "divider"
    ALERT = "alert"
    UNKNOWN = "unknown"


@dataclass
class ConfigValueOption(DataClassORJSONMixin):
    """An option for a select-type config entry."""

    label: str
    value: str


@dataclass
class ConfigEntry(DataClassORJSONMixin):
    """A rich configuration entry for visual editing.

    Used by both the component config system and section config editing.
    Inspired by the Music Assistant ConfigEntry pattern.
    """

    key: str
    type: ConfigEntryType
    label: str
    default_value: str | int | float | bool | None = None
    required: bool = False
    description: str | None = None
    options: list[ConfigValueOption] | None = None
    range: tuple[int | float, int | float] | None = None
    help_link: str | None = None
    multi_value: bool = False
    hidden: bool = False
    advanced: bool = False
    translation_key: str | None = None  # defaults to key if None
    translation_params: list[str] | None = None
    value: str | int | float | bool | list[str] | None = None
