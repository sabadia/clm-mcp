"""Curated cockpit dashboard tools: thin wrappers over `services.cockpit`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import cockpit
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
    async def clm_get_cockpit_weekly_counts(
        ctx: Context[AppContext],
        filter_schedule_start_date: Annotated[
            datetime, Field(description="Start of the schedule date window (inclusive).")
        ],
        filter_schedule_end_date: Annotated[
            datetime, Field(description="End of the schedule date window (inclusive).")
        ],
        site_id: _SiteIdParam = None,
    ) -> dict[str, Any]:
        """Weekly shipment count grid for a site's active unloading zones.

        The date window is capped at 92 days — the underlying API returns
        one entry per day with no size limit of its own, so a wider window
        raises a ToolError rather than returning an unbounded response.
        """
        app = ctx.request_context.lifespan_context
        return await cockpit.get_cockpit_weekly_counts(
            app,
            site_id=site_id,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_weather(
        ctx: Context[AppContext],
        start_date: Annotated[datetime, Field(description="Start of the date range.")],
        end_date: Annotated[datetime, Field(description="End of the date range.")],
        site_id: _SiteIdParam = None,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """Daily weather records (temperature, wind speed, symbol) stored
        for a site within a date range."""
        app = ctx.request_context.lifespan_context
        return await cockpit.get_weather(
            app, site_id=site_id, start_date=start_date, end_date=end_date, fields=fields
        )
