"""Curated konshub tools: thin wrappers over `services.konshub`
(ClmKonshubWebService — the warehouse/logistics dashboard: incoming/outgoing
shipments, deliveries, storage, warehouse zones).

Tool names carry a `konshub_` infix where they'd otherwise collide with a
shipment-service tool name (e.g. `clm_list_konshub_incoming_shipments`, not
`clm_list_incoming_shipments`) — the shipment service already owns the
unprefixed names.

Pagination here is **one-based** (`page_number=1` is the first page), unlike
the shipment/construction curated tools' zero-based convention — confirmed
live: `KonsHubShipmentQuery/GetDeliveriesTabShipmentList` computes
`skip = (PageNumber - 1) * PageSize` internally and rejects `PageNumber=0`
with a 500 (`Value is not greater than or equal to 0: -5. (Parameter
'skip')`), reproduced identically via raw `curl` against the live API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import konshub
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
_PageNumberParam = Annotated[
    int, Field(description="One-based page number (page 1 is first).", ge=1)
]
_PageSizeParam = Annotated[int, Field(description="Rows per page.", ge=1, le=200)]


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_konshub_incoming_shipments(
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
            Field(description="Shipment status codes to filter by. Omit for all statuses."),
        ] = None,
        page_number: _PageNumberParam = 1,
        page_size: _PageSizeParam = 50,
        order_by_field: Annotated[
            str | None, Field(description="Field name to sort by, if any.")
        ] = None,
        ascending: Annotated[bool, Field(description="Sort ascending if true.")] = True,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """Incoming shipments scheduled within a date window, for the
        KonsHub dashboard — restricted to the site's unloading zones."""
        app = ctx.request_context.lifespan_context
        return await konshub.list_incoming_shipments(
            app,
            site_id=site_id,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
            status=status,
            page_number=page_number,
            page_size=page_size,
            order_by_field=order_by_field,
            ascending=ascending,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_konshub_outgoing_shipments(
        ctx: Context[AppContext],
        filter_schedule_start_date: Annotated[
            datetime, Field(description="Start of the schedule date window (inclusive).")
        ],
        filter_schedule_end_date: Annotated[
            datetime, Field(description="End of the schedule date window (inclusive).")
        ],
        site_id: _SiteIdParam = None,
        page_number: _PageNumberParam = 1,
        page_size: _PageSizeParam = 50,
        order_by_field: Annotated[
            str | None, Field(description="Field name to sort by, if any.")
        ] = None,
        ascending: Annotated[bool, Field(description="Sort ascending if true.")] = True,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """Outgoing shipments scheduled within a date window, for the
        KonsHub dashboard, sorted by scheduled/set/ship date."""
        app = ctx.request_context.lifespan_context
        return await konshub.list_outgoing_shipments(
            app,
            site_id=site_id,
            filter_schedule_start_date=filter_schedule_start_date,
            filter_schedule_end_date=filter_schedule_end_date,
            page_number=page_number,
            page_size=page_size,
            order_by_field=order_by_field,
            ascending=ascending,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_konshub_deliveries(
        ctx: Context[AppContext],
        delivery_tab: Annotated[
            str,
            Field(
                description=(
                    "Which Deliveries-screen tab to list, e.g. 'Incoming', 'Unplanned', "
                    "'Planned', 'Commissioned', 'Completed'."
                )
            ),
        ],
        site_id: _SiteIdParam = None,
        global_search_value: Annotated[
            str | None, Field(description="Free-text search term, if any.")
        ] = None,
        page_number: _PageNumberParam = 1,
        page_size: _PageSizeParam = 50,
        order_by_field: Annotated[
            str | None, Field(description="Field name to sort by, if any.")
        ] = None,
        ascending: Annotated[bool, Field(description="Sort ascending if true.")] = True,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List shipments for one tab of the KonsHub Deliveries screen."""
        app = ctx.request_context.lifespan_context
        return await konshub.list_deliveries(
            app,
            site_id=site_id,
            delivery_tab=delivery_tab,
            global_search_value=global_search_value,
            page_number=page_number,
            page_size=page_size,
            order_by_field=order_by_field,
            ascending=ascending,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_count_konshub_deliveries(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        global_search_value: Annotated[
            str | None, Field(description="Free-text search term, if any.")
        ] = None,
    ) -> dict[str, Any]:
        """Shipment counts for each tab of the Deliveries screen (Incoming,
        Unplanned, Planned, Commissioned, Completed) plus a grand total."""
        app = ctx.request_context.lifespan_context
        return await konshub.count_deliveries(
            app, site_id=site_id, global_search_value=global_search_value
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_search_konshub_deliveries(
        ctx: Context[AppContext],
        search_text: Annotated[str, Field(description="Free-text search term.")],
        site_id: _SiteIdParam = None,
        page_number: _PageNumberParam = 1,
        page_size: _PageSizeParam = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """Free-text search over a site's deliveries spanning every tab in
        a single call — for when the caller only knows a shipment number
        or similar, not which tab it's currently on."""
        app = ctx.request_context.lifespan_context
        return await konshub.search_deliveries(
            app,
            site_id=site_id,
            search_text=search_text,
            page_number=page_number,
            page_size=page_size,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_konshub_shipment_comments(
        ctx: Context[AppContext],
        konshub_shipment_id: Annotated[str, Field(description="The KonsHub shipment's id.")],
    ) -> ShapedListResponse:
        """List all comments on a KonsHub shipment, each enriched with the
        creator's profile info."""
        app = ctx.request_context.lifespan_context
        return await konshub.get_shipment_comments(app, konshub_shipment_id=konshub_shipment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_storage_zones(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        warehouse_id: Annotated[
            str | None, Field(description="Restrict to this warehouse, if given.")
        ] = None,
        search_text: Annotated[
            str | None, Field(description="Free-text search term, if any.")
        ] = None,
    ) -> ShapedListResponse:
        """List storage zones (name, capacity, rows/pitches, warehouse/site
        info) matching an optional combination of filters."""
        app = ctx.request_context.lifespan_context
        return await konshub.list_storage_zones(
            app, site_id=site_id, warehouse_id=warehouse_id, search_text=search_text
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_warehouse_zones(
        ctx: Context[AppContext],
        warehouse_id: Annotated[
            str, Field(description="The warehouse's id (not a site id — see clm_whoami).")
        ],
    ) -> ShapedListResponse:
        """List zones (entry points, unloading zones, etc.) belonging to a
        warehouse."""
        app = ctx.request_context.lifespan_context
        return await konshub.list_warehouse_zones(app, warehouse_id=warehouse_id)
