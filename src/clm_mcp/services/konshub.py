"""KonsHub domain service functions (ClmKonshubWebService — the warehouse/
logistics dashboard layered on top of shipments).

Uses the canonical qualified `{service}/{Tag}/{PathTail}` operation names
throughout — see `services/construction.py`'s module docstring for why.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_incoming_shipments(
    app: AppContext,
    *,
    site_id: str | None,
    filter_schedule_start_date: datetime,
    filter_schedule_end_date: datetime,
    status: list[int] | None,
    page_number: int,
    page_size: int,
    order_by_field: str | None,
    ascending: bool,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "SiteId": resolved_site_id,
        "FilterScheduleStartDate": filter_schedule_start_date.isoformat(),
        "FilterScheduleEndDate": filter_schedule_end_date.isoformat(),
        "Status": status,
        "PageNumber": page_number,
        "PageSize": page_size,
        "OrderByField": order_by_field,
        "Ascending": ascending,
    }
    return await call_list(
        app,
        "konshub/KonsHubShipmentQuery/GetDashboardIncommingShipmentList",
        params,
        fields=fields,
    )


async def list_outgoing_shipments(
    app: AppContext,
    *,
    site_id: str | None,
    filter_schedule_start_date: datetime,
    filter_schedule_end_date: datetime,
    page_number: int,
    page_size: int,
    order_by_field: str | None,
    ascending: bool,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "SiteId": resolved_site_id,
        "FilterScheduleStartDate": filter_schedule_start_date.isoformat(),
        "FilterScheduleEndDate": filter_schedule_end_date.isoformat(),
        "PageNumber": page_number,
        "PageSize": page_size,
        "OrderByField": order_by_field,
        "Ascending": ascending,
    }
    return await call_list(
        app,
        "konshub/KonsHubShipmentQuery/GetDashboardOutgoingShipmentList",
        params,
        fields=fields,
    )


async def list_deliveries(
    app: AppContext,
    *,
    site_id: str | None,
    delivery_tab: str,
    global_search_value: str | None,
    page_number: int,
    page_size: int,
    order_by_field: str | None,
    ascending: bool,
    fields: list[str] | None,
) -> ShapedListResponse:
    """One tab of the Deliveries screen (Incoming, Unplanned, Planned,
    Commissioned, Completed)."""
    resolved_site_id = await resolve_site_id(app, site_id)
    params: dict[str, Any] = {
        "SiteId": resolved_site_id,
        "DeliveryTab": delivery_tab,
        "GlobalSearchValue": global_search_value,
        "PageNumber": page_number,
        "PageSize": page_size,
        "OrderByField": order_by_field,
        "Ascending": ascending,
    }
    return await call_list(
        app, "konshub/KonsHubShipmentQuery/GetDeliveriesTabShipmentList", params, fields=fields
    )


async def count_deliveries(
    app: AppContext, *, site_id: str | None, global_search_value: str | None
) -> dict[str, Any]:
    """Shipment counts for each tab of the Deliveries screen (Incoming,
    Unplanned, Planned, Commissioned, Completed) plus a grand total."""
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SiteId": resolved_site_id, "GlobalSearchValue": global_search_value}
    return await call_object(
        app, "konshub/KonsHubShipmentQuery/GetDeliveriesTabShipmentCount", params
    )


async def search_deliveries(
    app: AppContext,
    *,
    site_id: str | None,
    search_text: str,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    """Free-text search over a site's deliveries spanning every tab in a
    single call — built for a caller that only knows a shipment number or
    similar, not which tab a shipment is currently on."""
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "SearchText": search_text,
        "PageNumber": page_number,
        "PageSize": page_size,
    }
    return await call_list(
        app, "konshub/KonsHubShipmentQuery/SearchDeliveriesShipment", params, fields=fields
    )


async def get_shipment_comments(app: AppContext, *, konshub_shipment_id: str) -> ShapedListResponse:
    return await call_list(
        app,
        "konshub/KonsHubShipmentQuery/GetShipmentComments",
        {"KonsHubShipmentId": konshub_shipment_id},
    )


async def list_storage_zones(
    app: AppContext, *, site_id: str | None, warehouse_id: str | None, search_text: str | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SiteId": resolved_site_id, "WareHouseId": warehouse_id, "SearchText": search_text}
    return await call_list(app, "konshub/StorageCommissionQuery/GetStorageZones", params)


async def list_warehouse_zones(app: AppContext, *, warehouse_id: str) -> ShapedListResponse:
    """Zones (entry points, unloading zones, etc.) belonging to a warehouse.

    Scoped by `warehouse_id`, not `site_id` — a warehouse has no site
    default to fall back to, unlike every other tool in this module.
    """
    return await call_list(
        app, "konshub/WareHouseQuery/GetKonsHubZones", {"WareHouseId": warehouse_id}
    )
