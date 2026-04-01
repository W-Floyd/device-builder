"""Device-related data models."""

from __future__ import annotations

from dataclasses import dataclass, field

from mashumaro.mixins.orjson import DataClassORJSONMixin


@dataclass
class Device(DataClassORJSONMixin):
    """A configured ESPHome device."""

    name: str
    friendly_name: str
    configuration: str  # filename (e.g. "my_device.yaml")
    path: str  # full disk path
    comment: str | None = None
    address: str = ""
    web_port: int | None = None
    target_platform: str = "UNKNOWN"
    current_version: str = ""
    deployed_version: str = ""
    loaded_integrations: list[str] = field(default_factory=list)
    board_id: str = ""


@dataclass
class AdoptableDevice(DataClassORJSONMixin):
    """A discoverable device available for import/adoption."""

    name: str
    friendly_name: str
    package_import_url: str
    project_name: str
    project_version: str
    network: str
    ignored: bool


@dataclass
class DevicesResponse(DataClassORJSONMixin):
    """Response for devices/list command."""

    configured: list[Device]
    importable: list[AdoptableDevice]


@dataclass
class WizardResponse(DataClassORJSONMixin):
    """Response after creating a new device."""

    configuration: str


@dataclass
class UpdateDeviceResponse(DataClassORJSONMixin):
    """Response after updating device metadata."""

    name: str
    friendly_name: str
    comment: str | None
    board_id: str | None
