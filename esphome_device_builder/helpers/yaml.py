"""Utilities for appending blocks to ESPHome YAML config files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ComponentCatalogEntry


def _fill_template(template: str, fields: dict[str, Any]) -> str:
    """Replace {key} placeholders in a YAML template with field values."""
    result = template
    for key, value in fields.items():
        result = result.replace(f"{{{key}}}", str(value))
    lines = []
    for line in result.splitlines(keepends=True):
        if re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", line):
            continue
        lines.append(line)
    return "".join(lines)


def append_yaml_block(yaml_path: Path, block: str) -> str:
    """Append *block* to the YAML file at *yaml_path* and return the full new content."""
    current = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
    separator = "\n" if current and not current.endswith("\n\n") else ""
    new_content = current + separator + block
    yaml_path.write_text(new_content, encoding="utf-8")
    return new_content


def build_component_yaml(template: str, fields: dict[str, Any]) -> str:
    """Fill a component template and return the rendered YAML block (legacy)."""
    return _fill_template(template, fields)


def build_automation_yaml(
    yaml_path: Path,
    target_component_name: str,
    trigger: str,
    actions: list[dict[str, Any]],
) -> str:
    """Append an automation block to the named component and return full YAML."""
    current = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""

    action_lines = []
    for call in actions:
        action_id = call["action"]
        action_fields = call.get("fields", {})
        action_lines.append(f"        - {action_id}:")
        for k, v in action_fields.items():
            action_lines.append(f"            {k}: {v}")

    trigger_block = f"    {trigger}:\n" + "\n".join(action_lines) + "\n"

    name_pattern = re.compile(
        r"^(\s+name:\s+" + re.escape(target_component_name) + r"\s*)$",
        re.MULTILINE,
    )
    match = None
    for m in name_pattern.finditer(current):
        match = m

    if match:
        insert_pos = match.end()
        new_content = current[:insert_pos] + "\n" + trigger_block + current[insert_pos:]
    else:
        separator = "\n" if current and not current.endswith("\n\n") else ""
        new_content = (
            current + separator + f"# Automation for {target_component_name}\n" + trigger_block
        )

    yaml_path.write_text(new_content, encoding="utf-8")
    return new_content


# ---------------------------------------------------------------------------
# Structural component YAML generation
# ---------------------------------------------------------------------------

# Platform categories that use the list-under-platform pattern
_ENTITY_CATEGORIES = {
    "sensor",
    "binary_sensor",
    "switch",
    "light",
    "fan",
    "cover",
    "climate",
    "button",
    "number",
    "select",
    "text",
    "text_sensor",
    "lock",
    "valve",
    "media_player",
    "speaker",
    "microphone",
    "camera",
    "display",
    "touchscreen",
    "output",
    "datetime",
    "event",
    "update",
    "alarm_control_panel",
}


def _format_yaml_value(value: Any) -> str:
    """Format a Python value for YAML output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if value in ("true", "false", "null", "yes", "no", "on", "off"):
            return f'"{value}"'
        if value.startswith("!") or ":" in value or "#" in value:
            return f'"{value}"'
        return value
    return str(value)


def _generate_id(component_id: str, name: str | None = None) -> str:
    """Auto-generate a component ID from the component type and optional name."""
    if name:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        return f"{component_id}_{slug}"
    return component_id


def generate_component_yaml(
    component: ComponentCatalogEntry,
    fields: dict[str, Any],
    sub_entities: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Generate a YAML block for adding a component to a device config."""
    lines: list[str] = []
    category = component.category
    comp_id = component.id

    is_platform = category in _ENTITY_CATEGORIES

    if is_platform:
        lines.append(f"{category}:")
        lines.append(f"  - platform: {comp_id}")
        indent = "    "
    else:
        lines.append(f"{comp_id}:")
        indent = "  "

    for key, value in fields.items():
        if key == "id" and not value:
            value = _generate_id(comp_id, fields.get("name"))
        lines.append(f"{indent}{key}: {_format_yaml_value(value)}")

    if sub_entities:
        for sub_key, sub_fields in sub_entities.items():
            lines.append(f"{indent}{sub_key}:")
            for sk, sv in sub_fields.items():
                lines.append(f"{indent}  {sk}: {_format_yaml_value(sv)}")

    return "\n".join(lines)
