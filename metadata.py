"""Per-device metadata store for fields not tracked by ESPHome's StorageJSON.

Stores a JSON file at {config_dir}/.device-builder.json.
Currently tracks: board_id (catalog board association).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)
_METADATA_FILE = ".device-builder.json"


def _load(config_dir: Path) -> dict[str, Any]:
    path = config_dir / _METADATA_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(config_dir: Path, data: dict[str, Any]) -> None:
    path = config_dir / _METADATA_FILE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_board_id(config_dir: Path, filename: str) -> str:
    return _load(config_dir).get(filename, {}).get("board_id", "")


def set_device_metadata(
    config_dir: Path,
    filename: str,
    *,
    board_id: str | None = None,
    friendly_name: str | None = None,
    comment: str | None = None,
) -> None:
    data = _load(config_dir)
    entry = data.setdefault(filename, {})
    if board_id is not None:
        entry["board_id"] = board_id
    if friendly_name is not None:
        entry["friendly_name"] = friendly_name
    if comment is not None:
        entry["comment"] = comment
    _save(config_dir, data)


def get_device_metadata(config_dir: Path, filename: str) -> dict[str, Any]:
    return _load(config_dir).get(filename, {})


def remove_device_metadata(config_dir: Path, filename: str) -> None:
    data = _load(config_dir)
    data.pop(filename, None)
    _save(config_dir, data)


# ---------------------------------------------------------------------------
# Global user preferences (stored under "_preferences" key)
# ---------------------------------------------------------------------------

_PREFS_KEY = "_preferences"


def get_preferences(config_dir: Path) -> dict[str, Any]:
    return _load(config_dir).get(_PREFS_KEY, {})


def set_preferences(config_dir: Path, prefs: dict[str, Any]) -> dict[str, Any]:
    data = _load(config_dir)
    current = data.setdefault(_PREFS_KEY, {})
    current.update(prefs)
    _save(config_dir, data)
    return current
