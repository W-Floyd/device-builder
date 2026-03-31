"""Section config handler: get/update config entries for a YAML section."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from typing import Any

from aiohttp import web

from ..section_config import get_section_config
from .util import error_response, get_settings, json_response

routes = web.RouteTableDef()


@routes.get("/devices/{configuration}/section-config")
async def section_config(request: web.Request) -> web.Response:
    """Return ConfigEntry list with current values for a YAML section."""
    settings = get_settings(request)
    configuration = request.match_info["configuration"]
    section_key = request.query.get("key", "")

    if not section_key:
        return error_response("Missing 'key' query parameter")

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    loop = asyncio.get_running_loop()
    yaml_text = await loop.run_in_executor(
        None, lambda: path.read_text(encoding="utf-8") if path.exists() else ""
    )

    result = get_section_config(yaml_text, section_key)
    if result is None:
        return error_response(f"Unknown section: {section_key}", status=404)

    return json_response(asdict(result))


@routes.post("/devices/{configuration}/section-config")
async def update_section_config(request: web.Request) -> web.Response:
    """Update config values for a YAML section and return the new YAML."""
    settings = get_settings(request)
    configuration = request.match_info["configuration"]

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    section_key = body.get("section_key", "")
    values: dict[str, Any] = body.get("values", {})

    if not section_key:
        return error_response("Missing 'section_key'")

    loop = asyncio.get_running_loop()
    yaml_text = await loop.run_in_executor(
        None, lambda: path.read_text(encoding="utf-8") if path.exists() else ""
    )

    new_yaml = _update_yaml_section(yaml_text, section_key, values)

    await loop.run_in_executor(
        None, lambda: path.write_text(new_yaml, encoding="utf-8")
    )

    return json_response({"yaml": new_yaml})


def _update_yaml_section(yaml_text: str, section_key: str, values: dict[str, Any]) -> str:
    """Update values within a YAML section.

    This is text-based to preserve formatting and comments.
    For each key in values, find the matching line and update the value.
    If a key doesn't exist yet, append it to the section.
    """
    lines = yaml_text.splitlines(keepends=True)

    # Find section boundaries
    section_start = -1
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(section_key)}\s*:", line):
            section_start = i
            break

    if section_start == -1:
        return yaml_text  # Section not found — don't modify

    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i]
        if stripped and not stripped[0].isspace() and not stripped.startswith("#") and stripped.strip():
            section_end = i
            break

    # Track which keys we've updated
    updated_keys: set[str] = set()
    current_parent = ""
    base_indent = "  "  # Default indent for top-level section children

    for i in range(section_start + 1, section_end):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect indentation
        indent = line[: len(line) - len(line.lstrip())]

        # Handle nested parent (e.g. 'encryption:')
        parent_match = re.match(r"^(\s+)(\w[\w.]*)\s*:\s*$", line)
        if parent_match:
            current_parent = parent_match.group(2)
            base_indent = parent_match.group(1)
            continue

        # Handle key: value lines
        kv_match = re.match(r"^(\s+)(\w[\w.]*)\s*:\s*(.*)$", line)
        if kv_match:
            line_indent = kv_match.group(1)
            key = kv_match.group(2)
            full_key = f"{current_parent}.{key}" if current_parent else key

            if full_key in values:
                new_val = _format_yaml_value(values[full_key])
                lines[i] = f"{line_indent}{key}: {new_val}\n"
                updated_keys.add(full_key)
            continue

        # Handle list items (- key: value)
        list_match = re.match(r"^(\s*-\s+)(\w[\w.]*)\s*:\s*(.*)$", line)
        if list_match:
            prefix = list_match.group(1)
            key = list_match.group(2)
            current_parent = ""

            if key in values:
                new_val = _format_yaml_value(values[key])
                lines[i] = f"{prefix}{key}: {new_val}\n"
                updated_keys.add(key)
            continue

    # Append any new keys that weren't found in existing YAML
    append_lines = []
    for key, val in values.items():
        if key in updated_keys or key.startswith("_"):
            continue
        # Handle dotted keys (e.g. 'encryption.key')
        if "." in key:
            parent, child = key.rsplit(".", 1)
            append_lines.append(f"{base_indent}{parent}:\n")
            append_lines.append(f"{base_indent}  {child}: {_format_yaml_value(val)}\n")
        else:
            append_lines.append(f"{base_indent}{key}: {_format_yaml_value(val)}\n")

    if append_lines:
        # Find the last non-empty line in the section to insert after it
        insert_pos = section_end
        # Walk back from section_end to skip blank lines
        while insert_pos > section_start + 1 and not lines[insert_pos - 1].strip():
            insert_pos -= 1
        for line_str in reversed(append_lines):
            lines.insert(insert_pos, line_str)

    return "".join(lines)


def _format_yaml_value(value: Any) -> str:
    """Format a Python value for YAML output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Keep secret references as-is
        if value.startswith("!secret"):
            return value
        # Quote strings that might be ambiguous
        if value in ("true", "false", "yes", "no", "null", ""):
            return f'"{value}"'
        return value
    return str(value)
