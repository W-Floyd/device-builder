"""Component definitions loader.

Component definitions are stored as individual YAML files in this directory.
Each file defines a component type with its platforms, fields, and YAML templates.

To add a new component, create a new YAML file following the schema in any existing file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ..models import (
    ComponentCatalogResponse,
    ComponentField,
    ComponentPlatform,
    ComponentType,
)

_LOGGER = logging.getLogger(__name__)

_COMPONENTS_DIR = Path(__file__).parent


def _load_field(data: dict) -> ComponentField:
    """Load a ComponentField from a dict."""
    return ComponentField(
        key=data["key"],
        label=data["label"],
        type=data["type"],
        required=data.get("required", False),
        default=data.get("default"),
        options=data.get("options"),
    )


def _load_platform(data: dict) -> ComponentPlatform:
    """Load a ComponentPlatform from a dict."""
    return ComponentPlatform(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        yaml_template=data["yaml_template"],
        fields=[_load_field(f) for f in data.get("fields", [])],
    )


def load_component_catalog() -> ComponentCatalogResponse:
    """Load all component definitions from YAML files in this directory."""
    components: list[ComponentType] = []

    for yaml_file in sorted(_COMPONENTS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text())
            components.append(
                ComponentType(
                    id=data["id"],
                    name=data["name"],
                    description=data["description"],
                    docs_url=data.get("docs_url", ""),
                    icon=data.get("icon", ""),
                    platforms=[_load_platform(p) for p in data.get("platforms", [])],
                )
            )
        except Exception:
            _LOGGER.exception(
                "Failed to load component definition from %s", yaml_file.name
            )

    return ComponentCatalogResponse(components=components)
