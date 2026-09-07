"""Site equipment domain service functions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_equipment(app: AppContext, *, site_id: str | None) -> dict[str, Any]:
    """All site equipment, split into bookable/non-bookable groups (a
    single object, not a paged list) for the equipment management grid."""
    resolved_site_id = await resolve_site_id(app, site_id)
    return await call_object(
        app, "SiteEquipmentQuery/GetEquipmentListForGridView", {"SiteId": resolved_site_id}
    )


async def list_equipment_timeline(
    app: AppContext,
    *,
    site_id: str | None,
    filter_schedule_start_date: datetime,
    filter_schedule_end_date: datetime,
    page_number: int,
    page_size: int,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "FilterScheduleStartDate": filter_schedule_start_date.isoformat(),
        "FilterScheduleEndDate": filter_schedule_end_date.isoformat(),
        "PageNumber": page_number,
        "PageSize": page_size,
    }
    return await call_list(
        app, "SiteEquipmentQuery/GetEquipmentTimelineListView", params, fields=fields
    )
