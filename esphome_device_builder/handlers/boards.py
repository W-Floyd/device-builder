"""Board catalog API handlers."""

from __future__ import annotations

from aiohttp import web

from ..boards import BOARD_CATALOG
from .util import error_response, json_response

routes = web.RouteTableDef()


@routes.get("/boards")
async def list_boards(request: web.Request) -> web.Response:
    """List boards with optional search, filtering, and pagination.

    Query parameters:
        query:    Free-text search (matches name, description, manufacturer, tags)
        platform: Filter by platform (esp32, esp8266, rp2040, ...)
        variant:  Filter by ESP32 variant (esp32, esp32s3, esp32c3, ...)
        tag:      Filter by tag
        offset:   Pagination offset (default: 0)
        limit:    Page size (default: 50, max: 200)
    """
    query = request.query.get("query")
    platform = request.query.get("platform")
    variant = request.query.get("variant")
    tag = request.query.get("tag")
    offset = max(0, int(request.query.get("offset", "0")))
    limit = min(200, max(1, int(request.query.get("limit", "50"))))

    boards, total = BOARD_CATALOG.search(
        query=query,
        platform=platform,
        variant=variant,
        tag=tag,
        offset=offset,
        limit=limit,
    )

    return json_response(
        {
            "boards": [b.to_dict() for b in boards],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
    )


@routes.get("/boards/{board_id}")
async def get_board(request: web.Request) -> web.Response:
    """Get a single board by ID."""
    board_id = request.match_info["board_id"]
    board = BOARD_CATALOG.get_board(board_id)
    if board is None:
        return error_response(f"Board not found: {board_id}", status=404)
    return json_response(board.to_dict())
