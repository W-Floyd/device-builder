"""Board catalog controller."""

from __future__ import annotations

import logging
from typing import Any

from ..definitions import load_board_catalog
from ..helpers.api import api_command
from ..models import BoardCatalogEntry, PagedBoardsResponse

_LOGGER = logging.getLogger(__name__)


class BoardCatalog:
    """In-memory board catalog with search and pagination."""

    def __init__(self) -> None:
        """Initialize the board catalog."""
        self._boards: list[BoardCatalogEntry] = []

    def load(self) -> None:
        """Load boards from YAML definitions."""
        catalog = load_board_catalog()
        self._boards = list(catalog.boards)
        _LOGGER.info("Board catalog loaded: %d boards", len(self._boards))

    @api_command("boards/get_board")
    async def get_board(self, *, board_id: str, **kwargs: Any) -> BoardCatalogEntry | None:
        """Get a single board by ID."""
        for board in self._boards:
            if board.id == board_id:
                return board
        return None

    @api_command("boards/get_boards")
    async def get_boards(
        self,
        *,
        query: str | None = None,
        platform: str | None = None,
        variant: str | None = None,
        tag: str | None = None,
        offset: int = 0,
        limit: int = 50,
        **kwargs: Any,
    ) -> PagedBoardsResponse:
        """Get boards with optional filtering, search, and pagination."""
        results = self._boards

        if platform:
            results = [b for b in results if b.esphome.platform == platform]

        if variant:
            variant_lower = variant.lower()
            results = [
                b
                for b in results
                if b.esphome.variant and b.esphome.variant.lower() == variant_lower
            ]

        if tag:
            tag_lower = tag.lower()
            results = [b for b in results if tag_lower in b.tags]

        if query:
            query_lower = query.lower()
            results = [
                b
                for b in results
                if query_lower in b.name.lower()
                or query_lower in b.description.lower()
                or query_lower in b.manufacturer.lower()
                or query_lower in b.id.lower()
                or any(query_lower in t for t in b.tags)
            ]

        results = sorted(
            results,
            key=lambda b: (not b.featured, b.is_generic, b.name.lower()),
        )

        total = len(results)
        page = results[offset : offset + limit]
        return PagedBoardsResponse(boards=page, total=total, offset=offset, limit=limit)
