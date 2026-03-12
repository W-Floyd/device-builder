"""Automation catalog and add-automation handlers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from aiohttp import web

from ..catalogs import AUTOMATION_CATALOG
from ..models import AddAutomationResponse
from ..yaml_editor import build_automation_yaml
from .util import error_response, get_settings, json_response

routes = web.RouteTableDef()


@routes.get("/automations/catalog")
async def automation_catalog(request: web.Request) -> web.Response:
    return json_response(asdict(AUTOMATION_CATALOG))


@routes.post("/devices/{configuration}/automations")
async def add_automation(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configuration = request.match_info["configuration"]

    try:
        path = settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    if not path.exists():
        return error_response("Configuration not found", status=404)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON body")

    target = body.get("target_component_name", "")
    trigger = body.get("trigger", "")
    raw_actions = body.get("actions", [])

    if not target or not trigger:
        return error_response("target_component_name and trigger are required")

    # Normalise actions to list of dicts
    actions = [
        {"action": a.get("action", ""), "fields": a.get("fields", {})}
        for a in raw_actions
    ]

    loop = asyncio.get_running_loop()
    new_yaml = await loop.run_in_executor(
        None, build_automation_yaml, path, target, trigger, actions
    )

    return json_response(asdict(AddAutomationResponse(yaml=new_yaml)))
