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
    ComponentSubEntry,
    ConfigEntry,
    ConfigEntryType,
    ConfigValueOption,
    PagedComponentsResponse,
    PinFeature,
    PinMode,
)

_LOGGER = logging.getLogger(__name__)

_COMPONENTS_JSON = Path(__file__).resolve().parent.parent / "definitions" / "components.json"


class ComponentCatalog:
    """In-memory component catalog with search and pagination."""

    def __init__(self) -> None:
        self._components: list[ComponentCatalogEntry] = []
        self._by_id: dict[str, ComponentCatalogEntry] = {}

    def load(self) -> None:
        """
        Load components from the pre-generated JSON file.

        Logs a warning and leaves the catalog empty when the file is
        missing — run ``script/sync_components.py`` to (re)generate it.
        """
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
        """
        Return all component categories sorted by count (highest first).

        Each entry is a ``{id, name, count}`` dict suitable for direct
        use in the catalog UI's filter list.
        """
        counts: dict[str, int] = {}
        for comp in self._components:
            counts[comp.category] = counts.get(comp.category, 0) + 1
        return sorted(
            [
                {"id": str(cat), "name": str(cat).replace("_", " ").title(), "count": count}
                for cat, count in counts.items()
            ],
            key=lambda c: (-int(c["count"]), str(c["name"])),
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
        platform: str | None = None,
        offset: int = 0,
        limit: int = 50,
        **kwargs: Any,
    ) -> PagedComponentsResponse:
        """
        Get components with optional filtering, search, and pagination.

        ``query`` matches against the component id, name, and description.
        ``platform`` filters to components compatible with the given
        target platform — components with an empty ``supported_platforms``
        list are considered platform-agnostic and always included.
        """
        results = self._components

        if category:
            results = [c for c in results if c.category == category]

        if platform:
            results = [
                c for c in results if not c.supported_platforms or platform in c.supported_platforms
            ]

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


# ---------------------------------------------------------------------------
# JSON → model loaders
# ---------------------------------------------------------------------------


def _safe_enum(enum_cls: type, value: Any, default: Any | None = None) -> Any:
    """Coerce *value* to an enum member, returning *default* on failure."""
    if value is None or value == "":
        return default
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default


def _load_pin_features(raw: Any) -> list[PinFeature]:
    """Parse a list of pin-feature strings, dropping unknown values."""
    if not isinstance(raw, list):
        return []
    out: list[PinFeature] = []
    for item in raw:
        feat = _safe_enum(PinFeature, item)
        if feat is not None:
            out.append(feat)
    return out


def _load_options(raw: Any) -> list[ConfigValueOption] | None:
    """
    Normalise the JSON ``options`` field into ConfigValueOption objects.

    Accepts either a list of plain strings (each used as both label and
    value) or a list of ``{label, value}`` dicts.
    """
    if not isinstance(raw, list) or not raw:
        return None
    out: list[ConfigValueOption] = []
    for item in raw:
        if isinstance(item, str):
            out.append(ConfigValueOption(label=item, value=item))
        elif isinstance(item, dict):
            value = str(item.get("value", ""))
            label = str(item.get("label", value))
            out.append(ConfigValueOption(label=label, value=value))
    return out or None


def _load_config_entry(data: dict) -> ConfigEntry:
    """Load a ConfigEntry from its JSON representation."""
    range_val: tuple[int | float, int | float] | None = None
    raw_range = data.get("range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
        range_val = (raw_range[0], raw_range[1])

    return ConfigEntry(
        key=data["key"],
        type=_safe_enum(ConfigEntryType, data.get("type"), ConfigEntryType.UNKNOWN),
        label=data.get("label") or data["key"],
        description=data.get("description"),
        required=bool(data.get("required", False)),
        default_value=data.get("default_value"),
        options=_load_options(data.get("options")),
        range=range_val,
        multi_value=bool(data.get("multi_value", False)),
        templatable=bool(data.get("templatable", False)),
        depends_on=data.get("depends_on"),
        depends_on_value=data.get("depends_on_value"),
        depends_on_value_not=data.get("depends_on_value_not"),
        depends_on_component=data.get("depends_on_component"),
        pin_features=_load_pin_features(data.get("pin_features")),
        pin_mode=_safe_enum(PinMode, data.get("pin_mode")),
        advanced=bool(data.get("advanced", False)),
        hidden=bool(data.get("hidden", False)),
        help_link=data.get("help_link"),
        translation_key=data.get("translation_key"),
        translation_params=data.get("translation_params"),
    )


def _load_sub_entry(data: dict) -> ComponentSubEntry:
    """Load a ComponentSubEntry from its JSON representation."""
    return ComponentSubEntry(
        key=data["key"],
        platform_type=data["platform_type"],
        config_entries=[_load_config_entry(e) for e in data.get("config_entries", [])],
    )


def _load_component(data: dict) -> ComponentCatalogEntry:
    """Load a ComponentCatalogEntry from its JSON representation."""
    return ComponentCatalogEntry(
        id=data["id"],
        name=data.get("name", data["id"]),
        description=data.get("description", ""),
        category=_safe_enum(ComponentCategory, data.get("category"), ComponentCategory.MISC),
        docs_url=data.get("docs_url", ""),
        image_url=data.get("image_url", ""),
        dependencies=list(data.get("dependencies", [])),
        multi_conf=bool(data.get("multi_conf", False)),
        supported_platforms=list(data.get("supported_platforms", [])),
        config_entries=[_load_config_entry(e) for e in data.get("config_entries", [])],
        # Accept the old ``sub_entities`` key as well so a stale
        # components.json still loads. Sync writes the new key going forward.
        sub_entries=[
            _load_sub_entry(s) for s in (data.get("sub_entries") or data.get("sub_entities") or [])
        ],
    )
