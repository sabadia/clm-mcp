"""Cockpit dashboard domain service functions: weekly aggregates and the
site weather data the cockpit's charts overlay against shipment activity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def get_cockpit_weekly_counts(
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
    return await call_object(app, "ShipmentQuery/GetCockpitWeeklyShipmentCounts", params)


async def get_weather(
    app: AppContext,
    *,
    site_id: str | None,
    start_date: datetime,
    end_date: datetime,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "StartDate": start_date.isoformat(),
        "EndDate": end_date.isoformat(),
    }
    return await call_list(app, "MeteomaticsQuery/GetWeatherInfoFromRange", params, fields=fields)
