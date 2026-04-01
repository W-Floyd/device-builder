"""Shared aiohttp utilities: JSON helpers, CORS middleware, auth."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from aiohttp import web

from ..dashboard import DASHBOARD
from ..settings import DashboardSettings

_LOGGER = logging.getLogger(__name__)


def json_response(data: Any, status: int = 200) -> web.Response:
    """Return a JSON response, serialising dataclasses automatically."""
    if hasattr(data, "__dataclass_fields__"):
        body = asdict(data)
    else:
        body = data
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body),
    )


def error_response(message: str, status: int = 400) -> web.Response:
    return json_response({"error": message}, status)


def get_settings(request: web.Request) -> DashboardSettings:
    return request.app["settings"]


@web.middleware
async def cors_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """Permissive CORS for local development."""
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp
