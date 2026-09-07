"""Curated lean card (working package) tools: thin wrappers over
`services.lean_cards`."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.enums import LeanCardStatus
from clm_mcp.server import AppContext
from clm_mcp.services import lean_cards
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
    async def clm_list_working_packages(
        ctx: Context[AppContext],
        status: Annotated[
            LeanCardStatus | str | int,
            Field(
                description=(
                    "Lean card status filter — an int, or a name from clm_list_enums "
                    "(e.g. 'UNKNOWN_1')."
                )
            ),
        ],
        site_id: _SiteIdParam = None,
        page_number: Annotated[int, Field(description="Zero-based page number.", ge=0)] = 0,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List lean cards (working packages) for a site, filtered by status."""
        app = ctx.request_context.lifespan_context
        return await lean_cards.list_working_packages(
            app,
            site_id=site_id,
            status=status,
            page_number=page_number,
            page_size=page_size,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_count_working_packages(
        ctx: Context[AppContext], site_id: _SiteIdParam = None
    ) -> dict[str, Any]:
        """Count of lean cards (working packages) for a site, broken down
        by status (overdue, active, pending, completed)."""
        app = ctx.request_context.lifespan_context
        return await lean_cards.count_working_packages(app, site_id=site_id)
