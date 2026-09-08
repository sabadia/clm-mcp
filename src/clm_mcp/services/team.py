"""Team domain service functions (ClmTeamWebService).

Uses the canonical qualified `{service}/{Tag}/{PathTail}` operation names
throughout — see `services/construction.py`'s module docstring for why.
"""

from __future__ import annotations

from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_teams(
    app: AppContext,
    *,
    site_id: str | None,
    search_keys: list[dict[str, str]] | None,
    page_number: int,
    page_size: int,
    order_by_field: str | None,
    ascending: bool,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "SiteId": resolved_site_id,
        "SearchKeys": search_keys,
        "PageNumber": page_number,
        "PageSize": page_size,
        "OrderByField": order_by_field,
        "Ascending": ascending,
    }
    return await call_list(app, "team/ClmTeamQuery/GetTeamList", params, fields=fields)


async def list_team_members(
    app: AppContext,
    *,
    team_id: str,
    assigned: bool,
    site_ids: list[str] | None,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    """Team members currently assigned to `team_id`, or (with
    `assigned=False`) unassigned candidates eligible for assignment to it."""
    params: dict[str, Any] = {
        "TeamId": team_id,
        "Assigned": assigned,
        "SiteIds": site_ids,
        "PageNumber": page_number,
        "PageSize": page_size,
    }
    return await call_list(app, "team/ClmTeamQuery/GetTeamMembersForTeam", params, fields=fields)


async def list_vendor_teams(
    app: AppContext, *, site_id: str | None, search_text: str | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SiteId": resolved_site_id, "SearchText": search_text}
    return await call_list(app, "team/ClmTeamQuery/GetVendorTeams", params)


async def list_join_requests(
    app: AppContext,
    *,
    site_id: str | None,
    status: list[int] | None,
    team_id: str | None,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "SiteId": resolved_site_id,
        "Status": status,
        "TeamId": team_id,
        "PageNumber": page_number,
        "PageSize": page_size,
    }
    return await call_list(app, "team/ClmTeamQuery/GetJoinRequestList", params, fields=fields)


async def get_join_request(app: AppContext, *, request_id: str) -> dict[str, Any]:
    return await call_object(
        app, "team/ClmTeamQuery/GetJoinRequestDetails", {"RequestId": request_id}
    )


async def list_site_responsible_persons(
    app: AppContext, *, site_id: str | None, search_text: str | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SiteId": resolved_site_id, "SearchText": search_text}
    return await call_list(app, "team/ClmTeamQuery/GetSiteResponsiblePersons", params)


async def get_person_info(
    app: AppContext, *, user_id: str | None, person_id: str | None
) -> dict[str, Any]:
    """An extended profile combining Person, User, ClmTeamMember, and
    temporary-role data. Pass either `user_id` or `person_id`."""
    params = {"UserId": user_id, "PersonId": person_id}
    return await call_object(app, "team/ClmTeamQuery/GetExtendPersonInfo", params)


async def list_invitations(
    app: AppContext,
    *,
    site_id: str | None,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SiteId": resolved_site_id, "PageNumber": page_number, "PageSize": page_size}
    return await call_list(app, "team/ClmTeamQuery/GetInvitationList", params, fields=fields)
