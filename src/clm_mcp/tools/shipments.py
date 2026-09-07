"""Curated shipment tools: thin wrappers over `services.shipments`.

Each tool function does only Context plumbing and parameter mapping — the
actual request construction and response shaping live in the service layer
(see `services/shipments.py`, `services/common.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import shipments
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
    async def clm_search_shipments(
        ctx: Context[AppContext],
        search_key: Annotated[str, Field(description="Free-text search term.")],
        site_id: _SiteIdParam = None,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """Search shipments (native CLM and KonsHub-linked) by free text,
        scoped to a site."""
        app = ctx.request_context.lifespan_context
        return await shipments.search_shipments(
            app, search_key=search_key, site_id=site_id, fields=fields
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_shipments(
        ctx: Context[AppContext],
        filter_schedule_start_date: Annotated[
            datetime, Field(description="Start of the schedule date window (inclusive).")
        ],
        filter_schedule_end_date: Annotated[
            datetime, Field(description="End of the schedule date window (inclusive).")
        ],
        site_id: _SiteIdParam = None,
        status: Annotated[
            list[int] | None,
            Field(
                description=(
                    "Shipment status codes to filter by (see clm_list_enums for known "
                    "values). Omit for all statuses."
                )
            ),
        ] = None,
        page_number: Annotated[int, Field(description="Zero-based page number.", ge=0)] = 0,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        order_by_field: Annotated[
            str | None, Field(description="Field name to sort by, if any.")
        ] = None,
        ascending: Annotated[bool, Field(description="Sort ascending if true.")] = True,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List shipments for the cockpit list view: filtered, searched,
        ordered, and paged, scoped to the site and the caller's team
        visibility."""
        app = ctx.request_context.lifespan_context
        return await shipments.list_shipments(
            app,
            site_id=site_id,
            status=status,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
            page_number=page_number,
            page_size=page_size,
            order_by_field=order_by_field,
            ascending=ascending,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_count_shipments(
        ctx: Context[AppContext],
        filter_schedule_start_date: Annotated[
            datetime, Field(description="Start of the schedule date window (inclusive).")
        ],
        filter_schedule_end_date: Annotated[
            datetime, Field(description="End of the schedule date window (inclusive).")
        ],
        site_id: _SiteIdParam = None,
    ) -> dict[str, Any]:
        """Shipment counts for a site broken down by status bucket (open,
        approved, completed, cancelled/rejected)."""
        app = ctx.request_context.lifespan_context
        return await shipments.count_shipments(
            app,
            site_id=site_id,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_shipment(
        ctx: Context[AppContext],
        shipment_id: Annotated[str, Field(description="The shipment's id.")],
    ) -> dict[str, Any]:
        """Retrieve the full internal shipment record by id (up to 142
        possible fields, most absent for any given shipment). Prefer
        clm_get_shipment_summary for a smaller, external-facing view."""
        app = ctx.request_context.lifespan_context
        return await shipments.get_shipment(app, shipment_id=shipment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_shipment_summary(
        ctx: Context[AppContext],
        shipment_id: Annotated[str, Field(description="The shipment's id.")],
    ) -> dict[str, Any]:
        """Retrieve a shipment's smaller, external-facing summary (native
        CLM or KonsHub-linked)."""
        app = ctx.request_context.lifespan_context
        return await shipments.get_shipment_summary(app, shipment_id=shipment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_shipment_comments(
        ctx: Context[AppContext],
        shipment_id: Annotated[str, Field(description="The shipment's id.")],
    ) -> ShapedListResponse:
        """List all comments posted on a shipment, with commenter display info."""
        app = ctx.request_context.lifespan_context
        return await shipments.get_shipment_comments(app, shipment_id=shipment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_shipment_event_logs(
        ctx: Context[AppContext],
        item_id: Annotated[
            str, Field(description="The shipment (or related entity) id, as a UUID.")
        ],
    ) -> ShapedListResponse:
        """Retrieve the audit/event log history for a shipment or related
        connected entity, ordered chronologically."""
        app = ctx.request_context.lifespan_context
        return await shipments.get_shipment_event_logs(app, item_id=item_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_shipment_timeline(
        ctx: Context[AppContext],
        shipment_id: Annotated[str, Field(description="The shipment's id.")],
    ) -> dict[str, Any]:
        """Retrieve the day-wise timeline for a single shipment."""
        app = ctx.request_context.lifespan_context
        return await shipments.get_shipment_timeline(app, shipment_id=shipment_id)
