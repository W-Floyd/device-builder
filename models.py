"""Dataclass models matching the TypeScript API interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


def to_dict(obj: Any) -> dict[str, Any]:
    """Recursively convert a dataclass to a JSON-serialisable dict."""
    return asdict(obj)


# ---------------------------------------------------------------------------
# Device models
# ---------------------------------------------------------------------------


@dataclass
class ConfiguredDevice:
    name: str
    friendly_name: str
    configuration: str
    path: str
    comment: str | None
    address: str
    web_port: int | None
    target_platform: str
    current_version: str
    deployed_version: str
    loaded_integrations: list[str]
    board_id: str = ""


@dataclass
class AdoptableDevice:
    name: str
    friendly_name: str
    package_import_url: str
    project_name: str
    project_version: str
    network: str
    ignored: bool


@dataclass
class DevicesResponse:
    configured: list[ConfiguredDevice]
    importable: list[AdoptableDevice]


@dataclass
class WizardRequest:
    name: str
    ssid: str
    psk: str
    type: str  # "basic" | "upload" | "empty"
    platform: str | None = None
    board: str | None = None
    password: str | None = None
    file_content: str | None = None
    board_id: str | None = None  # catalog board id (new field)


@dataclass
class WizardResponse:
    configuration: str


@dataclass
class UpdateDeviceRequest:
    friendly_name: str | None = None
    comment: str | None = None
    board_id: str | None = None


@dataclass
class UpdateDeviceResponse:
    name: str
    friendly_name: str
    comment: str | None
    board_id: str | None


@dataclass
class ImportRequest:
    name: str
    project_name: str
    package_import_url: str
    friendly_name: str | None = None
    encryption: str | None = None


@dataclass
class IgnoreDeviceRequest:
    name: str
    ignore: bool


# ---------------------------------------------------------------------------
# Board models
# ---------------------------------------------------------------------------


@dataclass
class Board:
    name: str
    board: str


@dataclass
class BoardCatalogEntry:
    id: str
    name: str
    description: str
    platform: str
    board: str
    tags: list[str]
    docs_url: str
    image_url: str | None
    contents: list[str] | None = None


@dataclass
class BoardCatalogResponse:
    boards: list[BoardCatalogEntry]


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------


@dataclass
class ComponentField:
    key: str
    label: str
    type: str  # "string" | "number" | "boolean" | "select" | "pin"
    required: bool
    default: str | int | bool | None = None
    options: list[str] | None = None


@dataclass
class ConfigValueOption:
    label: str
    value: str


@dataclass
class ConfigEntry:
    """A rich configuration entry for visual editing of YAML sections."""

    key: str
    type: str  # "boolean" | "string" | "secure_string" | "integer" | "float" | "label" | "divider" | "select" | "icon" | "alert"
    label: str
    default_value: str | int | float | bool | None = None
    required: bool = False
    options: list[ConfigValueOption] | None = None
    range: list[int | float] | None = None
    description: str | None = None
    help_link: str | None = None
    multi_value: bool = False
    hidden: bool = False
    value: str | int | float | bool | list[str] | None = None


@dataclass
class SectionConfigResponse:
    """Response from GET /devices/{config}/section-config."""

    section_key: str
    section_type: str  # "core" | "component" | "automation"
    title: str
    description: str
    docs_url: str
    icon: str
    entries: list[ConfigEntry]


@dataclass
class ComponentPlatform:
    id: str
    name: str
    description: str
    yaml_template: str
    fields: list[ComponentField]


@dataclass
class ComponentType:
    id: str
    name: str
    description: str
    docs_url: str
    icon: str
    platforms: list[ComponentPlatform]


@dataclass
class ComponentCatalogResponse:
    components: list[ComponentType]


@dataclass
class AddComponentRequest:
    component: str
    platform: str
    fields: dict[str, Any]


@dataclass
class AddComponentResponse:
    yaml: str


# ---------------------------------------------------------------------------
# Automation models
# ---------------------------------------------------------------------------


@dataclass
class AutomationTrigger:
    id: str
    name: str
    description: str
    applicable_to: list[str]
    fields: list[ComponentField]


@dataclass
class AutomationAction:
    id: str
    name: str
    description: str
    fields: list[ComponentField]


@dataclass
class AutomationCatalogResponse:
    triggers: list[AutomationTrigger]
    actions: list[AutomationAction]


@dataclass
class AutomationActionCall:
    action: str
    fields: dict[str, Any]


@dataclass
class AddAutomationRequest:
    target_component_name: str
    trigger: str
    actions: list[AutomationActionCall]


@dataclass
class AddAutomationResponse:
    yaml: str


# ---------------------------------------------------------------------------
# Config section models
# ---------------------------------------------------------------------------


@dataclass
class ConfigSection:
    id: str
    name: str
    description: str
    docs_url: str
    icon: str
    yaml_template: str
    fields: list[ComponentField]


@dataclass
class ConfigCatalogResponse:
    sections: list[ConfigSection]


@dataclass
class AddConfigSectionRequest:
    section: str
    fields: dict[str, Any]


@dataclass
class AddConfigSectionResponse:
    yaml: str


# ---------------------------------------------------------------------------
# Utility models
# ---------------------------------------------------------------------------


@dataclass
class VersionResponse:
    version: str


@dataclass
class SerialPort:
    port: str
    desc: str


@dataclass
class DownloadItem:
    title: str
    file: str


@dataclass
class UserPreferences:
    editor_layout: str = "both"  # "both" | "left" | "right"
