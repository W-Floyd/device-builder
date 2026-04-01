#!/usr/bin/env python3
"""Validate board and component definition manifests.

Checks that all manifest.yaml files in the definitions directory
have the required fields and valid structure.

Used as a pre-commit hook and in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

try:
    import jsonschema

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

DEFINITIONS_DIR = Path(__file__).resolve().parent.parent / "esphome_device_builder" / "definitions"
SCHEMAS_DIR = DEFINITIONS_DIR / "schemas"

# Load JSON schemas if jsonschema is available
_BOARD_SCHEMA: dict | None = None
_COMPONENT_SCHEMA: dict | None = None

if HAS_JSONSCHEMA:
    _board_schema_path = SCHEMAS_DIR / "board.schema.json"
    if _board_schema_path.exists():
        _BOARD_SCHEMA = json.loads(_board_schema_path.read_text())

    _component_schema_path = SCHEMAS_DIR / "component.schema.json"
    if _component_schema_path.exists():
        _COMPONENT_SCHEMA = json.loads(_component_schema_path.read_text())


def _validate_against_schema(data: dict, schema: dict | None, item_id: str) -> list[str]:
    """Validate data against a JSON schema. Returns error messages."""
    if not HAS_JSONSCHEMA or schema is None:
        return []
    errors: list[str] = []
    for error in jsonschema.Draft7Validator(schema).iter_errors(data):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{item_id}: schema error at {path}: {error.message}")
    return errors


def validate_board(manifest: Path) -> list[str]:
    """Validate a board manifest. Returns list of error messages."""
    errors: list[str] = []
    board_id = manifest.parent.name

    try:
        data = yaml.safe_load(manifest.read_text())
    except yaml.YAMLError as exc:
        return [f"{board_id}: invalid YAML: {exc}"]

    if not isinstance(data, dict):
        return [f"{board_id}: manifest is not a YAML mapping"]

    # JSON Schema validation
    errors.extend(_validate_against_schema(data, _BOARD_SCHEMA, board_id))
    if errors:
        return errors  # schema errors are comprehensive, skip manual checks

    # Extra checks beyond schema
    # ID must match folder name
    if data.get("id") and data["id"] != board_id:
        errors.append(f"{board_id}: id '{data['id']}' does not match folder name")

    # Duplicate GPIO check (schema can't do cross-item uniqueness)
    pins = data.get("pins", [])
    if isinstance(pins, list):
        seen_gpios: set[int] = set()
        for pin in pins:
            if isinstance(pin, dict) and (gpio := pin.get("gpio")) is not None:
                if gpio in seen_gpios:
                    errors.append(f"{board_id}: duplicate gpio {gpio}")
                seen_gpios.add(gpio)

    return errors


def validate_component(manifest: Path) -> list[str]:
    """Validate a component manifest. Returns list of error messages."""
    errors: list[str] = []
    comp_id = manifest.parent.name

    try:
        data = yaml.safe_load(manifest.read_text())
    except yaml.YAMLError as exc:
        return [f"{comp_id}: invalid YAML: {exc}"]

    if not isinstance(data, dict):
        return [f"{comp_id}: manifest is not a YAML mapping"]

    # JSON Schema validation
    errors.extend(_validate_against_schema(data, _COMPONENT_SCHEMA, comp_id))
    if errors:
        return errors

    return errors


def main() -> int:
    """Validate all definitions. Returns 0 on success, 1 on errors."""
    all_errors: list[str] = []

    # Validate boards
    boards_dir = DEFINITIONS_DIR / "boards"
    for manifest in sorted(boards_dir.glob("*/manifest.yaml")):
        all_errors.extend(validate_board(manifest))

    # Validate components
    components_dir = DEFINITIONS_DIR / "components"
    for manifest in sorted(components_dir.glob("*/manifest.yaml")):
        all_errors.extend(validate_component(manifest))

    if all_errors:
        for error in all_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"\n{len(all_errors)} error(s) found", file=sys.stderr)
        return 1

    board_count = len(list(boards_dir.glob("*/manifest.yaml")))
    comp_count = len(list(components_dir.glob("*/manifest.yaml")))
    print(f"OK: {board_count} boards, {comp_count} components validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
