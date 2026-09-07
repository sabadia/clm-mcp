"""Incident domain service functions."""

from __future__ import annotations

from typing import Any

from clm_mcp.server import AppContext
from clm_mcp.services.common import call_list, call_object
from clm_mcp.spec.shaping import ShapedListResponse


async def list_incidents(app: AppContext, *, shipment_id: str) -> ShapedListResponse:
    return await call_list(app, "IncidentQuery/GetIncidentList", {"ShipmentId": shipment_id})


async def get_incident(app: AppContext, *, item_id: str) -> dict[str, Any]:
    return await call_object(app, "IncidentQuery/GetIncidentDetail", {"ItemId": item_id})
