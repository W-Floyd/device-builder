"""Board catalog and platform board list handlers."""

from __future__ import annotations

from dataclasses import asdict

from aiohttp import web

from ..catalogs import BOARD_CATALOG, get_boards_for_platform
from ..models import BoardCatalogResponse
from .util import error_response, json_response

routes = web.RouteTableDef()


@routes.get("/boards/catalog")
async def board_catalog(request: web.Request) -> web.Response:
    return json_response(asdict(BOARD_CATALOG))


@routes.get("/boards/{platform}")
async def boards_for_platform(request: web.Request) -> web.Response:
    platform = request.match_info["platform"]
    boards = get_boards_for_platform(platform)
    if not boards:
        return error_response(f"Unknown or unsupported platform: {platform}", status=404)
    return json_response([asdict(b) for b in boards])
