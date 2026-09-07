"""Material handover domain service functions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object, resolve_site_id
from clm_mcp.spec.shaping import ShapedListResponse


async def list_material_handovers(
    app: AppContext,
    *,
    site_id: str | None,
    status: str,
    start_date: datetime,
    end_date: datetime,
    fields: list[str] | None,
) -> ShapedListResponse:
    resolved_site_id = await resolve_site_id(app, site_id)
    params = {
        "SiteId": resolved_site_id,
        "Status": status,
        "StartDate": start_date.isoformat(),
        "EndDate": end_date.isoformat(),
    }
    return await call_list(
        app, "MaterialHandoverQuery/GetMaterialHandoverList", params, fields=fields
    )


async def get_material_handover(app: AppContext, *, handover_id: str) -> dict[str, Any]:
    return await call_object(
        app, "MaterialHandoverQuery/GetMaterialHandover", {"HandoverId": handover_id}
    )
