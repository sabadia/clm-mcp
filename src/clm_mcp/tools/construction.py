"""Curated construction tools: thin wrappers over `services.construction`
(ClmConstructionWebService — materials, zones, site structure, wiki search).

`clm_list_materials`'s pagination is **one-based** (`page_number=1` is the
first page), unlike this module's other (unpaginated or GET-based) tools —
confirmed live: `ConstructionManagementQuery/GetMaterialList` computes
`skip = (PageNumber - 1) * PageSize` internally and rejects `PageNumber=0`
with a 500 (`Value is not greater than or equal to 0: -5. (Parameter
'skip')`) once real data exists on the target site to trigger the
calculation — on an otherwise-empty site the same call instead throws a
NullReferenceException regardless of `PageNumber`, which initially looked
like a page-number-independent bug until a well-configured site (with real
materials) exposed the real cause.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import construction
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
_SearchKeysParam = Annotated[
    list[dict[str, str]] | None,
    Field(
        description=(
            'Structured search filters, e.g. [{"FieldName": "Name", "Value": "cement"}]. '
            "Omit for no filtering."
        )
    ),
]


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_materials(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        search_keys: _SearchKeysParam = None,
        page_number: Annotated[
            int, Field(description="One-based page number (page 1 is first).", ge=1)
        ] = 1,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        order_by_field: Annotated[
            str | None, Field(description="Field name to sort by, if any.")
        ] = None,
        ascending: Annotated[bool, Field(description="Sort ascending if true.")] = True,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List materials for a site: searchable by name, unit, description,
        team, and shipment counts."""
        app = ctx.request_context.lifespan_context
        return await construction.list_materials(
            app,
            site_id=site_id,
            search_keys=search_keys,
            page_number=page_number,
            page_size=page_size,
            order_by_field=order_by_field,
            ascending=ascending,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_material(
        ctx: Context[AppContext],
        item_id: Annotated[str, Field(description="The material's item id.")],
    ) -> dict[str, Any]:
        """Retrieve a single material by its item id."""
        app = ctx.request_context.lifespan_context
        return await construction.get_material(app, item_id=item_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_material_usage(
        ctx: Context[AppContext],
        material_id: Annotated[str, Field(description="The material's id.")],
    ) -> dict[str, Any]:
        """A material's shipment usage totals, grouped by status (open,
        approved, completed)."""
        app = ctx.request_context.lifespan_context
        return await construction.get_material_usage(app, material_id=material_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_zones(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        is_active: Annotated[
            bool | None, Field(description="Filter to only active zones, if given.")
        ] = None,
    ) -> ShapedListResponse:
        """List zones belonging to a site or shared at the project level,
        each enriched with its direction way-points."""
        app = ctx.request_context.lifespan_context
        return await construction.list_zones(app, site_id=site_id, is_active=is_active)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_zone(
        ctx: Context[AppContext],
        zone_id: Annotated[str, Field(description="The zone's id.")],
    ) -> dict[str, Any]:
        """Retrieve a zone by id, with its equipment, direction way-points,
        and related waiting areas or unloading zones."""
        app = ctx.request_context.lifespan_context
        return await construction.get_zone(app, zone_id=zone_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_site_locations(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        is_active: Annotated[
            bool | None,
            Field(description="Filter out inactive unloading zones as of `date`, if given."),
        ] = None,
        date: Annotated[
            datetime | None, Field(description="Reference date for the `is_active` filter.")
        ] = None,
    ) -> ShapedListResponse:
        """List a site's zones as locations, each enriched with direction
        way-points."""
        app = ctx.request_context.lifespan_context
        return await construction.list_site_locations(
            app, site_id=site_id, is_active=is_active, date=date
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_site_structure(
        ctx: Context[AppContext], site_id: _SiteIdParam = None
    ) -> dict[str, Any]:
        """A site's structure (buildings/floors/laydowns), assembled into
        one hierarchical response."""
        app = ctx.request_context.lifespan_context
        return await construction.get_site_structure(app, site_id=site_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_search_wiki(
        ctx: Context[AppContext],
        search_text: Annotated[str, Field(description="Free-text search term.")],
        top_n: Annotated[int, Field(description="Maximum number of results.", ge=1, le=100)] = 10,
    ) -> ShapedListResponse:
        """Search wiki page content by keyword."""
        app = ctx.request_context.lifespan_context
        return await construction.search_wiki(app, search_text=search_text, top_n=top_n)
