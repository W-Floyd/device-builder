"""Common/shared data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


@dataclass
class ConfigValueOption(DataClassORJSONMixin):
    label: str
    value: str


@dataclass
class ConfigEntry(DataClassORJSONMixin):
    """A rich configuration entry for visual editing of YAML sections."""

    key: str
    # boolean, string, secure_string, integer, float, label, divider, select, icon, alert
    type: str
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
class SectionConfigResponse(DataClassORJSONMixin):
    """Response from GET /devices/{config}/section-config."""

    section_key: str
    section_type: str  # "core" | "component" | "automation"
    title: str
    description: str
    docs_url: str
    icon: str
    entries: list[ConfigEntry]


@dataclass
class ComponentField(DataClassORJSONMixin):
    key: str
    label: str
    type: str
    required: bool
    default: str | int | bool | None = None
    options: list[str] | None = None


@dataclass
class AutomationTrigger(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    applicable_to: list[str]
    fields: list[ComponentField]


@dataclass
class AutomationAction(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    fields: list[ComponentField]


@dataclass
class AutomationCatalogResponse(DataClassORJSONMixin):
    triggers: list[AutomationTrigger]
    actions: list[AutomationAction]


@dataclass
class AutomationActionCall(DataClassORJSONMixin):
    action: str
    fields: dict[str, Any]


@dataclass
class AddAutomationRequest(DataClassORJSONMixin):
    target_component_name: str
    trigger: str
    actions: list[AutomationActionCall]


@dataclass
class AddAutomationResponse(DataClassORJSONMixin):
    yaml: str


@dataclass
class ConfigSection(DataClassORJSONMixin):
    id: str
    name: str
    description: str
    docs_url: str
    icon: str
    yaml_template: str
    fields: list[ComponentField]


@dataclass
class ConfigCatalogResponse(DataClassORJSONMixin):
    sections: list[ConfigSection]


@dataclass
class AddConfigSectionRequest(DataClassORJSONMixin):
    section: str
    fields: dict[str, Any]


@dataclass
class AddConfigSectionResponse(DataClassORJSONMixin):
    yaml: str


@dataclass
class VersionResponse(DataClassORJSONMixin):
    version: str


@dataclass
class SerialPort(DataClassORJSONMixin):
    port: str
    desc: str


@dataclass
class DownloadItem(DataClassORJSONMixin):
    title: str
    file: str


@dataclass
class UserPreferences(DataClassORJSONMixin):
    editor_layout: str = "both"  # "both" | "left" | "right"
