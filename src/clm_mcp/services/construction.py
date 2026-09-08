"""Construction domain service functions (ClmConstructionWebService).

Uses the canonical qualified `{service}/{Tag}/{PathTail}` operation names
throughout (unlike the pre-multi-service shipment modules, which predate
`spec/registry.py`'s service qualifier and rely on `get_operation`'s
unqualified-alias fallback — see `spec/registry.py::OperationRegistry.resolve`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_materials(
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
    return await call_list(
        app, "construction/ConstructionManagementQuery/GetMaterialList", params, fields=fields
    )


async def get_material(app: AppContext, *, item_id: str) -> dict[str, Any]:
    return await call_object(
        app, "construction/ConstructionManagementQuery/GetMaterialById", {"ItemId": item_id}
    )


async def get_material_usage(app: AppContext, *, material_id: str) -> dict[str, Any]:
    """A material's shipment usage totals, grouped by status (open,
    approved, completed).

    This operation is noticeably slower than most (confirmed live: ~9s for
    a real material) and always reports `IsSuccess: false` even on success
    — `api/envelope.py` special-cases exactly this operation to treat a
    populated `Data` as success regardless (a null `Data` still means the
    material genuinely doesn't exist).
    """
    return await call_object(
        app,
        "construction/ConstructionManagementQuery/GetMaterialUsagesById",
        {"MaterialId": material_id},
    )


async def list_zones(
    app: AppContext, *, site_id: str | None, is_active: bool | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    # GET operation: query parameter names are the spec's own lower-camelCase
    # (`siteId`, `isActive`) — not the PascalCase used by POST request bodies.
    params: dict[str, Any] = {"siteId": resolved_site_id, "isActive": is_active}
    return await call_list(app, "construction/ConstructionManagementQuery/GetZones", params)


async def get_zone(app: AppContext, *, zone_id: str) -> dict[str, Any]:
    return await call_object(
        app, "construction/ConstructionManagementQuery/GetZoneById", {"zoneId": zone_id}
    )


async def list_site_locations(
    app: AppContext, *, site_id: str | None, is_active: bool | None, date: datetime | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "siteId": resolved_site_id,
        "isActive": is_active,
        "date": date.isoformat() if date is not None else None,
    }
    return await call_list(app, "construction/ConstructionManagementQuery/GetSiteLocations", params)


async def get_site_structure(app: AppContext, *, site_id: str | None) -> dict[str, Any]:
    """A site's structure (buildings/floors/laydowns), assembled into one
    hierarchical response."""
    resolved_site_id = await resolve_site_id(app, site_id)
    return await call_object(
        app,
        "construction/ConstructionManagementQuery/GetSiteStructuresBySite",
        {"SiteId": resolved_site_id},
    )


async def search_wiki(app: AppContext, *, search_text: str, top_n: int) -> ShapedListResponse:
    params = {"SearchText": search_text, "TopN": top_n}
    return await call_list(app, "construction/WikiQuery/SearchWikiContentByKeyword", params)
