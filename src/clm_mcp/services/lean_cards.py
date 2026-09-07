"""Lean card (working package) domain service functions."""

from __future__ import annotations

from typing import Any

from clm_mcp.enums import LeanCardStatus
from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_enum_param, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_working_packages(
    app: AppContext,
    *,
    site_id: str | None,
    status: LeanCardStatus | str | int,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "Status": resolve_enum_param(LeanCardStatus, status),
        "PageNumber": page_number,
        "PageSize": page_size,
    }
    return await call_list(app, "LeanCardQuery/GetWorkingPackages", params, fields=fields)


async def count_working_packages(app: AppContext, *, site_id: str | None) -> dict[str, Any]:
    resolved_site_id = await resolve_site_id(app, site_id)
    return await call_object(
        app, "LeanCardQuery/GetWorkingPackagesCount", {"SiteId": resolved_site_id}
    )
