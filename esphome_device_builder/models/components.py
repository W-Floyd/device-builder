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
class ComponentSubEntry(DataClassORJSONMixin):
    """
    A nested sub-configuration inside a parent component.

    Two shapes share this dataclass:

    - **Entity sub-entries** — platform components that produce
      multiple readings or instances. A DHT sensor block produces both
      a temperature and a humidity entity; ``platform_type`` is set
      ("sensor") so the frontend knows to apply platform-default
      fields like name / device_class.
    - **Nested config groups** — opaque sub-dicts in the schema like
      ``esp32_ble_tracker.scan_parameters`` that just bundle related
      settings without representing entities. ``platform_type`` is
      None so the frontend renders them as a plain collapsible group.
    """

    # YAML key under the parent component (e.g. "temperature",
    # "scan_parameters").
    key: str

    # Platform type the sub-entry represents (e.g. "sensor",
    # "binary_sensor"). None when the sub-entry is just a nested
    # config group rather than an entity definition.
    platform_type: str | None = None

    # Sub-entry-specific config fields beyond the platform defaults.
    config_entries: list[ConfigEntry] = field(default_factory=list)


@dataclass
class ComponentCatalogEntry(DataClassORJSONMixin):
    """A component in the catalog.

    Components map 1:1 to ESPHome's `components/` directory. Each entry
    describes how to render and serialize one block in the user's YAML
    config (e.g. `wifi:`, `sensor:`, `i2c:`).
    """

    # Component ID — matches ESPHome's component directory name and the
    # YAML key the user types (e.g. "wifi", "dht", "i2c").
    id: str

    # Human-readable name shown in the UI ("Wi-Fi", "DHT Temperature
    # & Humidity Sensor", "I²C Bus").
    name: str

    # Description shown on the component card and detail view. Sourced
    # from the ESPHome docs frontmatter and first paragraph.
    description: str

    # Group the component is filed under in the catalog UI.
    category: ComponentCategory

    # Direct link to the official ESPHome docs page for this component.
    docs_url: str = ""

    # Optional image / illustration shown on the component card.
    image_url: str = ""

    # Other components this one requires to be configured. ESPHome
    # rejects the YAML if a dependency is missing — the frontend should
    # warn the user and offer to add the missing component.
    dependencies: list[str] = field(default_factory=list)

    # Whether the same component can be added multiple times (e.g.
    # multiple sensors, multiple I²C buses). When False, the component
    # is a singleton (e.g. `wifi:`, `api:`).
    multi_conf: bool = False

    # Empty list = component works on every target platform. Non-empty
    # = component is restricted to those platforms (e.g. ["esp32"] for
    # ESP32-only hardware features). Frontend uses this to filter the
    # available components based on the device's selected board.
    supported_platforms: list[str] = field(default_factory=list)

    # The component's own configuration fields.
    config_entries: list[ConfigEntry] = field(default_factory=list)

    # Nested configurations the component exposes (see ComponentSubEntry).
    sub_entries: list[ComponentSubEntry] = field(default_factory=list)


@dataclass
class AddComponentRequest(DataClassORJSONMixin):
    """Request to add a component to a device config."""

    component_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    sub_entries: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AddComponentResponse(DataClassORJSONMixin):
    """Response after adding a component."""

    yaml: str


@dataclass
class PagedComponentsResponse(PagedResponse):
    """Paginated component catalog API response."""

    components: list[ComponentCatalogEntry] = field(default_factory=list)
    categories: list[dict[str, str | int]] = field(default_factory=list)
