"""Cockpit dashboard domain service functions: weekly aggregates and the
site weather data the cockpit's charts overlay against shipment activity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse

# GetCockpitWeeklyShipmentCounts returns one entry per *day* in the window,
# nested inside a single object response — unlike every list-returning tool
# (see services/common.py::call_list), that response never goes through
# shape_list_response's byte-cap truncation. Verified live: a 7-year window
# returns 2,558 daily entries totalling ~530,000 bytes (>10x
# CLM_MAX_RESPONSE_BYTES's default), with no truncation applied at all. A
# "weekly" grid has no legitimate need for a multi-year window, so this caps
# it outright rather than trying to retrofit generic truncation onto a
# single nested array inside an otherwise object-shaped response.
_MAX_DAILY_COUNT_WINDOW_DAYS = 92


async def get_cockpit_weekly_counts(
    app: AppContext,
    *,
    site_id: str | None,
    filter_schedule_start_date: datetime,
    filter_schedule_end_date: datetime,
) -> dict[str, Any]:
    window = filter_schedule_end_date - filter_schedule_start_date
    if window.days > _MAX_DAILY_COUNT_WINDOW_DAYS:
        raise ToolError(
            f"filter_schedule_start_date/filter_schedule_end_date span {window.days} days, "
            f"exceeding the {_MAX_DAILY_COUNT_WINDOW_DAYS}-day limit for this tool — the "
            "underlying API returns one entry per day with no size cap, so a wide window "
            "produces an unbounded response. Narrow the date range (a 'weekly' cockpit view "
            "rarely needs more than a few months at once)."
        )
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
