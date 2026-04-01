"""Component catalog and add-component handlers."""

from __future__ import annotations

import asyncio
import json

from aiohttp import web

from ..catalogs import COMPONENT_CATALOG
from ..models import AddComponentResponse
from ..yaml_editor import append_yaml_block, build_component_yaml
from .util import error_response, get_settings, json_response

routes = web.RouteTableDef()


@routes.get("/components/catalog")
async def component_catalog(request: web.Request) -> web.Response:
    return json_response(COMPONENT_CATALOG).to_dict()


@routes.post("/devices/{configuration}/components")
async def add_component(request: web.Request) -> web.Response:
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

    component_id = body.get("component", "")
    platform_id = body.get("platform", "")
    fields = body.get("fields", {})

    # Find the template from the catalog
    comp = next((c for c in COMPONENT_CATALOG.components if c.id == component_id), None)
    if comp is None:
        return error_response(f"Unknown component: {component_id}", status=404)

    plat = next((p for p in comp.platforms if p.id == platform_id), None)
    if plat is None:
        return error_response(f"Unknown platform: {platform_id}", status=404)

    block = build_component_yaml(plat.yaml_template, fields)

    loop = asyncio.get_running_loop()
    new_yaml = await loop.run_in_executor(None, append_yaml_block, path, block)

    return json_response(AddComponentResponse(yaml=new_yaml).to_dict())
