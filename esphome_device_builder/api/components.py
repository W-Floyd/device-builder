"""Component catalog and device component management API handlers."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from ..controllers.components import COMPONENT_CATALOG
from ..helpers.json import error_response, get_settings, json_response
from ..models import AddComponentResponse
from ..yaml_editor import generate_component_yaml

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.get("/components")
async def list_components(request: web.Request) -> web.Response:
    """List components with optional search, filtering, and pagination.

    Query parameters:
        query:    Free-text search (matches name, description, id)
        category: Filter by category (sensor, binary_sensor, switch, ...)
        offset:   Pagination offset (default: 0)
        limit:    Page size (default: 50, max: 200)
    """
    result = COMPONENT_CATALOG.get_components(
        query=request.query.get("query"),
        category=request.query.get("category"),
        offset=max(0, int(request.query.get("offset", "0"))),
        limit=min(200, max(1, int(request.query.get("limit", "50")))),
    )
    return json_response(result.to_dict())


@routes.get("/components/{component_id}")
async def get_component(request: web.Request) -> web.Response:
    """Get a single component by ID with full config entries."""
    component_id = request.match_info["component_id"]
    component = COMPONENT_CATALOG.get_component(component_id)
    if component is None:
        return error_response(f"Component not found: {component_id}", status=404)
    return json_response(component.to_dict())


@routes.post("/devices/{configuration}/components")
async def add_component(request: web.Request) -> web.Response:
    """Add a component to a device configuration.

    Body:
        component_id: str - the component to add
        fields: dict - config field values
        sub_entities: dict - sub-entity field values
    """
    settings = get_settings(request)
    configuration = request.match_info["configuration"]

    try:
        settings.rel_path(configuration)
    except ValueError:
        return error_response("Forbidden", status=403)

    body = await request.json()
    component_id = body.get("component_id", "")

    component = COMPONENT_CATALOG.get_component(component_id)
    if component is None:
        return error_response(f"Unknown component: {component_id}")

    fields = body.get("fields", {})
    sub_entities = body.get("sub_entities", {})

    # Validate required fields
    for entry in component.config_entries:
        if entry.required and entry.key not in fields:
            return error_response(f"Missing required field: {entry.key}")

    # Generate YAML
    yaml_block = generate_component_yaml(component, fields, sub_entities)

    # Read existing config and append
    config_path = settings.rel_path(configuration)
    loop = asyncio.get_running_loop()

    existing = await loop.run_in_executor(None, config_path.read_text)
    new_yaml = existing.rstrip() + "\n\n" + yaml_block + "\n"
    await loop.run_in_executor(None, config_path.write_text, new_yaml)

    return json_response(AddComponentResponse(yaml=new_yaml).to_dict())
