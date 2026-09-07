"""Curated site equipment tools: thin wrappers over `services.equipment`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import equipment
from clm_mcp.spec.shaping import ShapedListResponse

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)

_FieldsParam = Annotated[
    list[str] | None,
    Field(description="Only include these top-level fields in each returned row."),
]
_SiteIdParam = Annotated[
    str | None,
    Field(description="Site to query. Defaults to the authenticated caller's own site."),
]


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_equipment(
        ctx: Context[AppContext], site_id: _SiteIdParam = None
    ) -> dict[str, Any]:
        """List all site equipment, split into bookable and non-bookable
        groups, for the equipment management grid view."""
        app = ctx.request_context.lifespan_context
        return await equipment.list_equipment(app, site_id=site_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_equipment_timeline(
        ctx: Context[AppContext],
        filter_schedule_start_date: Annotated[
            datetime, Field(description="Start of the schedule date window (inclusive).")
        ],
        filter_schedule_end_date: Annotated[
            datetime, Field(description="End of the schedule date window (inclusive).")
        ],
        site_id: _SiteIdParam = None,
        page_number: Annotated[int, Field(description="Zero-based page number.", ge=0)] = 0,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List equipment timeline entries (bookings) for the cockpit
        equipment list view: filtered, searched, sorted, and paged."""
        app = ctx.request_context.lifespan_context
        return await equipment.list_equipment_timeline(
            app,
            site_id=site_id,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
            page_number=page_number,
            page_size=page_size,
            fields=fields,
        )
