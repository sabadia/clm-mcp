"""Curated team tools: thin wrappers over `services.team`
(ClmTeamWebService — teams, members, join requests, invitations, contacts).

Pagination here is **one-based** (`page_number=1` is the first page), unlike
the shipment/construction curated tools' zero-based convention — confirmed
live: `ClmTeamQuery/GetJoinRequestList` (and `GetTeamList`) compute
`skip = (PageNumber - 1) * PageSize` internally and reject `PageNumber=0`
with a 500 (`Value is not greater than or equal to 0: -5. (Parameter
'skip')`), reproduced identically via raw `curl` against the live API.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from clm_mcp.server import AppContext
from clm_mcp.services import team
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
            'Structured search filters, e.g. [{"FieldName": "Name", "Value": "acme"}]. '
            "Omit for no filtering."
        )
    ),
]


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_teams(
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
        """List CLM teams for a site, surfacing the caller's own teams first
        by default."""
        app = ctx.request_context.lifespan_context
        return await team.list_teams(
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
    async def clm_list_team_members(
        ctx: Context[AppContext],
        team_id: Annotated[str, Field(description="The team's id.")],
        assigned: Annotated[
            bool,
            Field(
                description=(
                    "True for members currently assigned to the team; false for unassigned "
                    "candidates eligible for assignment to it."
                )
            ),
        ] = True,
        site_ids: Annotated[
            list[str] | None, Field(description="Restrict to these sites, if given.")
        ] = None,
        page_number: Annotated[
            int, Field(description="One-based page number (page 1 is first).", ge=1)
        ] = 1,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List members of (or unassigned candidates for) a team."""
        app = ctx.request_context.lifespan_context
        return await team.list_team_members(
            app,
            team_id=team_id,
            assigned=assigned,
            site_ids=site_ids,
            page_number=page_number,
            page_size=page_size,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_vendor_teams(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        search_text: Annotated[
            str | None, Field(description="Free-text search term, if any.")
        ] = None,
    ) -> ShapedListResponse:
        """List vendor teams for a site."""
        app = ctx.request_context.lifespan_context
        return await team.list_vendor_teams(app, site_id=site_id, search_text=search_text)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_join_requests(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        status: Annotated[
            list[int] | None,
            Field(description="Join-request status codes to filter by. Omit for all statuses."),
        ] = None,
        team_id: Annotated[
            str | None, Field(description="Restrict to this team, if given.")
        ] = None,
        page_number: Annotated[
            int, Field(description="One-based page number (page 1 is first).", ge=1)
        ] = 1,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List team join requests for a site, role-scoped to what the
        caller is authorized to review."""
        app = ctx.request_context.lifespan_context
        return await team.list_join_requests(
            app,
            site_id=site_id,
            status=status,
            team_id=team_id,
            page_number=page_number,
            page_size=page_size,
            fields=fields,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_join_request(
        ctx: Context[AppContext],
        request_id: Annotated[str, Field(description="The join request's id.")],
    ) -> dict[str, Any]:
        """Full detail of a single team join request."""
        app = ctx.request_context.lifespan_context
        return await team.get_join_request(app, request_id=request_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_site_responsible_persons(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        search_text: Annotated[
            str | None, Field(description="Free-text search term, if any.")
        ] = None,
    ) -> ShapedListResponse:
        """List responsible-person contacts for a site (e.g. for shipment
        sender/recipient pickers)."""
        app = ctx.request_context.lifespan_context
        return await team.list_site_responsible_persons(
            app, site_id=site_id, search_text=search_text
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_get_person_info(
        ctx: Context[AppContext],
        user_id: Annotated[
            str | None, Field(description="A user id — pass this or person_id.")
        ] = None,
        person_id: Annotated[
            str | None, Field(description="A person id — pass this or user_id.")
        ] = None,
    ) -> dict[str, Any]:
        """An extended profile combining Person, User, ClmTeamMember, and
        temporary-role data for a given user/person."""
        app = ctx.request_context.lifespan_context
        return await team.get_person_info(app, user_id=user_id, person_id=person_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def clm_list_invitations(
        ctx: Context[AppContext],
        site_id: _SiteIdParam = None,
        page_number: Annotated[
            int, Field(description="One-based page number (page 1 is first).", ge=1)
        ] = 1,
        page_size: Annotated[int, Field(description="Rows per page.", ge=1, le=200)] = 50,
        fields: _FieldsParam = None,
    ) -> ShapedListResponse:
        """List pending and past user invitations for a site."""
        app = ctx.request_context.lifespan_context
        return await team.list_invitations(
            app, site_id=site_id, page_number=page_number, page_size=page_size, fields=fields
        )
