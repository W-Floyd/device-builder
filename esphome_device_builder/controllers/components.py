"""Component catalog controller."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..helpers.api import api_command
from ..models import (
    ComponentCatalogEntry,
    ComponentCategory,
    ComponentSubEntity,
    ConfigEntry,
    ConfigEntryType,
    PagedComponentsResponse,
)

_LOGGER = logging.getLogger(__name__)

_COMPONENTS_JSON = Path(__file__).resolve().parent.parent / "definitions" / "components.json"


def _load_config_entry(data: dict) -> ConfigEntry:
    """Load a ConfigEntry from a dict."""
    range_val = None
    raw_range = data.get("range")
    if raw_range and isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
        range_val = (raw_range[0], raw_range[1])

    return ConfigEntry(
        key=data["key"],
        type=ConfigEntryType(data.get("type", "unknown")),
        label=data.get("label", data["key"]),
        required=data.get("required", False),
        default_value=data.get("default_value"),
        description=data.get("description"),
        options=data.get("options"),
        range=range_val,
        advanced=data.get("advanced", False),
    )


def _load_sub_entity(data: dict) -> ComponentSubEntity:
    """Load a ComponentSubEntity from a dict."""
    return ComponentSubEntity(
        key=data["key"],
        platform_type=data["platform_type"],
        config_entries=[_load_config_entry(e) for e in data.get("config_entries", [])],
    )


def _load_component(data: dict) -> ComponentCatalogEntry:
    """Load a ComponentCatalogEntry from a dict."""
    try:
        category = ComponentCategory(data.get("category", "misc"))
    except ValueError:
        category = ComponentCategory.MISC

    return ComponentCatalogEntry(
        id=data["id"],
        name=data.get("name", data["id"]),
        description=data.get("description", ""),
        category=category,
        docs_url=data.get("docs_url", ""),
        image_url=data.get("image_url", ""),
        dependencies=data.get("dependencies", []),
        auto_load=data.get("auto_load", []),
        multi_conf=data.get("multi_conf", False),
        config_entries=[_load_config_entry(e) for e in data.get("config_entries", [])],
        sub_entities=[_load_sub_entity(s) for s in data.get("sub_entities", [])],
    )


class ComponentCatalog:
    """In-memory component catalog with search and pagination."""

    def __init__(self) -> None:
        """Initialize the component catalog."""
        self._components: list[ComponentCatalogEntry] = []
        self._by_id: dict[str, ComponentCatalogEntry] = {}

    def load(self) -> None:
        """Load components from the pre-generated JSON file."""
        if not _COMPONENTS_JSON.exists():
            _LOGGER.warning(
                "Component catalog not found at %s — run script/sync_components.py",
                _COMPONENTS_JSON,
            )
            return

        data = json.loads(_COMPONENTS_JSON.read_text())
        self._components = [_load_component(c) for c in data.get("components", [])]
        self._by_id = {c.id: c for c in self._components}
        _LOGGER.info("Component catalog loaded: %d components", len(self._components))

    @property
    def categories(self) -> list[dict[str, str | int]]:
        """Return category list with counts."""
        counts: dict[str, int] = {}
        for comp in self._components:
            counts[comp.category] = counts.get(comp.category, 0) + 1
        return sorted(
            [
                {"id": cat, "name": cat.replace("_", " ").title(), "count": count}
                for cat, count in counts.items()
            ],
            key=lambda c: (-c["count"], c["name"]),
        )

    @api_command("components/get_categories")
    async def get_categories(self, **kwargs: Any) -> list[dict[str, str | int]]:
        """Get all component categories with counts."""
        return self.categories

    @api_command("components/get_component")
    async def get_component(
        self, *, component_id: str, **kwargs: Any
    ) -> ComponentCatalogEntry | None:
        """Get a single component by ID."""
        return self._by_id.get(component_id)

    @api_command("components/get_components")
    async def get_components(
        self,
        *,
        query: str | None = None,
        category: ComponentCategory | str | None = None,
        offset: int = 0,
        limit: int = 50,
        **kwargs: Any,
    ) -> PagedComponentsResponse:
        """Get components with optional filtering, search, and pagination."""
        results = self._components

        if category:
            results = [c for c in results if c.category == category]

        if query:
            query_lower = query.lower()
            results = [
                c
                for c in results
                if query_lower in c.name.lower()
                or query_lower in c.description.lower()
                or query_lower in c.id.lower()
            ]

        total = len(results)
        page = results[offset : offset + limit]
        return PagedComponentsResponse(
            components=page,
            total=total,
            offset=offset,
            limit=limit,
            categories=self.categories,
        )
