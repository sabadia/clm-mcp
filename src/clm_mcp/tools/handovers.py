"""Curated material handover tools: thin wrappers over `services.handovers`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import handovers
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
    async def clm_list_material_handovers(
        ctx: Context[AppContext],
        start_date: Annotated[datetime, Field(description="Start of the date window.")],
        end_date: Annotated[datetime, Field(description="End of the date window.")],
        status: Annotated[
            str,
            Field(
                description=(
                    "Handover status filter — required and must be non-empty (the API "
                    "rejects null/blank), and matched as a literal value, not a "
                    "wildcard: 'All' is accepted without a validation error but "
                    "matches zero handovers (confirmed live: TotalCount=0 against a "
                    "site with 35 real handovers). Not documented as an enum in the "
                    "spec; 'InProgress' is confirmed live to filter correctly "
                    "(returned all 35 matching handovers on that same site). Other "
                    "candidate values ('Open', 'Completed', 'Pending', 'Draft', "
                    "'Closed') were accepted without a validation error but their "
                    "filtering semantics were not confirmed against real data."
                )
            ),
        ],
        site_id: _SiteIdParam = None,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List material handover summaries for a site, filtered by status
        and date range."""
        app = ctx.request_context.lifespan_context
        return await handovers.list_material_handovers(
            app,
            site_id=site_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_material_handover(
        ctx: Context[AppContext],
        handover_id: Annotated[str, Field(description="The material handover's id.")],
    ) -> dict[str, Any]:
        """Full details of a single material handover, including its event
        history and equipment usage."""
        app = ctx.request_context.lifespan_context
        return await handovers.get_material_handover(app, handover_id=handover_id)
