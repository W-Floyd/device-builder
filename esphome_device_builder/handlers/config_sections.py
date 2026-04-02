"""Config section catalog and add-config-section handlers."""

from __future__ import annotations

import asyncio
import json

from aiohttp import web

from ..catalogs import CONFIG_CATALOG
from ..models import AddConfigSectionResponse
from ..yaml_editor import append_yaml_block, build_component_yaml
from .util import error_response, get_settings, json_response

routes = web.RouteTableDef()


@routes.get("/config/catalog")
async def config_catalog(request: web.Request) -> web.Response:
    return json_response(CONFIG_CATALOG)


@routes.post("/devices/{configuration}/config-sections")
async def add_config_section(request: web.Request) -> web.Response:
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

    section_id = body.get("section", "")
    fields = body.get("fields", {})

    section = next((s for s in CONFIG_CATALOG.sections if s.id == section_id), None)
    if section is None:
        return error_response(f"Unknown config section: {section_id}", status=404)

    block = build_component_yaml(section.yaml_template, fields)

    loop = asyncio.get_running_loop()
    new_yaml = await loop.run_in_executor(None, append_yaml_block, path, block)

    return json_response(AddConfigSectionResponse(yaml=new_yaml).to_dict())
