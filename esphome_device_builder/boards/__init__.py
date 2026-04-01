"""Board definitions loader.

Board definitions are stored as individual YAML files in this directory.
Each file defines a single board with its metadata, platform, and PlatformIO board ID.

To add a new board, create a new YAML file following the schema in any existing file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ..models import BoardCatalogEntry, BoardCatalogResponse

_LOGGER = logging.getLogger(__name__)

_BOARDS_DIR = Path(__file__).parent


def load_board_catalog() -> BoardCatalogResponse:
    """Load all board definitions from YAML files in this directory."""
    boards: list[BoardCatalogEntry] = []

    for yaml_file in sorted(_BOARDS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text())
            boards.append(
                BoardCatalogEntry(
                    id=data["id"],
                    name=data["name"],
                    description=data["description"],
                    platform=data["platform"],
                    board=data["board"],
                    tags=data.get("tags", []),
                    docs_url=data.get("docs_url", ""),
                    image_url=data.get("image_url"),
                    contents=data.get("contents"),
                )
            )
        except Exception:
            _LOGGER.exception("Failed to load board definition from %s", yaml_file.name)

    return BoardCatalogResponse(boards=boards)
