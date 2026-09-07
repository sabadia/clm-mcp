"""Shipment domain service functions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def search_shipments(
    app: AppContext, *, search_key: str, site_id: str | None, fields: list[str] | None
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {"SearchKey": search_key, "SiteId": resolved_site_id}
    return await call_list(app, "ShipmentQuery/SearchShipment", params, fields=fields)


async def list_shipments(
    app: AppContext,
    *,
    site_id: str | None,
    status: list[int] | None,
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
        "Status": status,
        "FilterScheduleStartDate": filter_schedule_start_date.isoformat(),
        "FilterScheduleEndDate": filter_schedule_end_date.isoformat(),
        "PageNumber": page_number,
        "PageSize": page_size,
        "OrderByField": order_by_field,
        "Ascending": ascending,
    }
    return await call_list(app, "ShipmentQuery/GetShipmentsForListView", params, fields=fields)


async def count_shipments(
    app: AppContext,
    *,
    site_id: str | None,
    filter_schedule_start_date: datetime,
    filter_schedule_end_date: datetime,
) -> dict[str, Any]:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "FilterScheduleStartDate": filter_schedule_start_date.isoformat(),
        "FilterScheduleEndDate": filter_schedule_end_date.isoformat(),
    }
    return await call_object(app, "ShipmentQuery/GetShipmentsCountForListView", params)


async def get_shipment(app: AppContext, *, shipment_id: str) -> dict[str, Any]:
    return await call_object(app, "ShipmentQuery/GetShipmentById", {"ShipmentId": shipment_id})


async def get_shipment_summary(app: AppContext, *, shipment_id: str) -> dict[str, Any]:
    """The reduced, external-facing shipment representation — much smaller
    than `get_shipment`'s full `Shipment` DTO (142 properties); prefer this
    when the caller doesn't need every internal field."""
    return await call_object(
        app, "ShipmentQuery/GetShipmentForExternal", {"ShipmentId": shipment_id}
    )


async def get_shipment_comments(app: AppContext, *, shipment_id: str) -> ShapedListResponse:
    return await call_list(app, "ShipmentQuery/GetShipmentComments", {"ShipmentId": shipment_id})


async def get_shipment_event_logs(app: AppContext, *, item_id: str) -> ShapedListResponse:
    """Audit/event log history for a shipment (or a related connected
    entity — this is a GET endpoint keyed by `itemId`, not `shipmentId`)."""
    return await call_list(app, "ShipmentQuery/GetEventLogs", {"itemId": item_id})


async def get_shipment_timeline(app: AppContext, *, shipment_id: str) -> dict[str, Any]:
    return await call_object(
        app, "TimelineQuery/GetShipmentTimelineById", {"ShipmentId": shipment_id}
    )
