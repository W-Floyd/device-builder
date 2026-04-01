"""Component catalog data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from .common import ConfigEntry, PagedResponse


class ComponentCategory(StrEnum):
    """Component categories (ESPHome platform types + infrastructure)."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    LIGHT = "light"
    FAN = "fan"
    COVER = "cover"
    CLIMATE = "climate"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"
    TEXT = "text"
    TEXT_SENSOR = "text_sensor"
    LOCK = "lock"
    VALVE = "valve"
    MEDIA_PLAYER = "media_player"
    SPEAKER = "speaker"
    MICROPHONE = "microphone"
    CAMERA = "camera"
    DISPLAY = "display"
    TOUCHSCREEN = "touchscreen"
    OUTPUT = "output"
    DATETIME = "datetime"
    EVENT = "event"
    UPDATE = "update"
    ALARM = "alarm_control_panel"
    CORE = "core"
    BUS = "bus"
    AUTOMATION = "automation"
    MISC = "misc"


@dataclass
class ComponentSubEntity(DataClassORJSONMixin):
    """A sub-entity provided by a component (e.g. DHT's temperature/humidity sensors)."""

    key: str
    platform_type: str
    config_entries: list[ConfigEntry] = field(default_factory=list)


@dataclass
class ComponentCatalogEntry(DataClassORJSONMixin):
    """A component in the catalog."""

    id: str
    name: str
    description: str
    category: ComponentCategory
    docs_url: str = ""
    image_url: str = ""
    dependencies: list[str] = field(default_factory=list)
    auto_load: list[str] = field(default_factory=list)
    multi_conf: bool = False
    config_entries: list[ConfigEntry] = field(default_factory=list)
    sub_entities: list[ComponentSubEntity] = field(default_factory=list)


@dataclass
class AddComponentRequest(DataClassORJSONMixin):
    """Request to add a component to a device config."""

    component_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    sub_entities: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AddComponentResponse(DataClassORJSONMixin):
    """Response after adding a component."""

    yaml: str


@dataclass
class PagedComponentsResponse(PagedResponse):
    """Paginated component catalog API response."""

    components: list[ComponentCatalogEntry] = field(default_factory=list)
    categories: list[dict[str, str | int]] = field(default_factory=list)
