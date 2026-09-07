"""Curated incident tools: thin wrappers over `services.incidents`."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import incidents
from clm_mcp.spec.shaping import ShapedListResponse

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_incidents(
        ctx: Context[AppContext],
        shipment_id: Annotated[str, Field(description="The shipment's id.")],
    ) -> ShapedListResponse:
        """List all non-deleted incidents/findings raised against a shipment."""
        app = ctx.request_context.lifespan_context
        return await incidents.list_incidents(app, shipment_id=shipment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_incident(
        ctx: Context[AppContext],
        item_id: Annotated[str, Field(description="The incident's item id.")],
    ) -> dict[str, Any]:
        """Retrieve the full detail of a single incident/finding."""
        app = ctx.request_context.lifespan_context
        return await incidents.get_incident(app, item_id=item_id)
