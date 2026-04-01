"""Utilities for appending blocks to ESPHome YAML config files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _fill_template(template: str, fields: dict[str, Any]) -> str:
    """Replace {key} placeholders in a YAML template with field values."""
    result = template
    for key, value in fields.items():
        result = result.replace(f"{{{key}}}", str(value))
    # Remove any unfilled optional placeholders (lines whose only content is an unfilled key)
    lines = []
    for line in result.splitlines(keepends=True):
        # If the line still has an unfilled {placeholder}, skip it
        if re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", line):
            continue
        lines.append(line)
    return "".join(lines)


def append_yaml_block(yaml_path: Path, block: str) -> str:
    """Append *block* to the YAML file at *yaml_path* and return the full new content."""
    current = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
    # Ensure there is a blank line separator before the new block
    separator = "\n" if current and not current.endswith("\n\n") else ""
    new_content = current + separator + block
    yaml_path.write_text(new_content, encoding="utf-8")
    return new_content


def build_component_yaml(template: str, fields: dict[str, Any]) -> str:
    """Fill a component template and return the rendered YAML block."""
    return _fill_template(template, fields)


def build_automation_yaml(
    yaml_path: Path,
    target_component_name: str,
    trigger: str,
    actions: list[dict[str, Any]],
) -> str:
    """Append an automation block to the named component and return full YAML.

    This appends an ``on_*:`` trigger block to the *last* occurrence of the
    component named *target_component_name* in the YAML file.  The approach is
    purely text-based to avoid disturbing existing YAML formatting.
    """
    current = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""

    # Build the actions YAML indented for the trigger block
    action_lines = []
    for call in actions:
        action_id = call["action"]
        action_fields = call.get("fields", {})
        action_lines.append(f"        - {action_id}:")
        for k, v in action_fields.items():
            action_lines.append(f"            {k}: {v}")

    trigger_block = f"    {trigger}:\n" + "\n".join(action_lines) + "\n"

    # Find insertion point: after the line containing `name: <target>` inside a list item
    name_pattern = re.compile(
        r"^(\s+name:\s+" + re.escape(target_component_name) + r"\s*)$",
        re.MULTILINE,
    )
    match = None
    for m in name_pattern.finditer(current):
        match = m  # use last match

    if match:
        insert_pos = match.end()
        new_content = current[:insert_pos] + "\n" + trigger_block + current[insert_pos:]
    else:
        # Fall back: append at end
        separator = "\n" if current and not current.endswith("\n\n") else ""
        new_content = (
            current + separator + f"# Automation for {target_component_name}\n" + trigger_block
        )

    yaml_path.write_text(new_content, encoding="utf-8")
    return new_content
