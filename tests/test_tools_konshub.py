"""Integration tests for the curated konshub tools, exercised through the
MCP SDK's in-process Client against a real build_server() + register_all(),
with the identity and business API endpoints mocked via respx.
"""

from __future__ import annotations

import time
from collections.abc import Generator

import httpx
import jwt
import pytest
import respx
from mcp.client.client import Client

from clm_mcp.config import Settings
from clm_mcp.server import build_server
from clm_mcp.tools import register_all

IDENTITY_URL = "https://clm.selisestage.com/api/identity/v25/identity/token"
KONSHUB_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-konshub"
SITE_ID = "68BC0C11-963F-46CB-AF93-267B50ABCCAF"


def make_settings() -> Settings:
    return Settings(refresh_token="rt", identity_token_url=IDENTITY_URL)


def make_jwt() -> str:
    payload = {
        "sub": "user-1",
        "user_id": "user-1",
        "site_id": SITE_ID,
        "role": ["admin"],
        "iat": int(time.time()),
        "exp": int(time.time() + 420),
    }
    return jwt.encode(payload, key="test-signing-key-at-least-32-bytes-long!!", algorithm="HS256")


def query_success(data: object, total_count: int = 0) -> dict[str, object]:
    return {
        "Data": data,
        "IsSuccess": True,
        "StatusCode": 0,
        "ErrorMessage": None,
        "PropertyName": None,
        "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "TotalCount": total_count,
    }


@pytest.fixture
def mock_router() -> Generator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": make_jwt(),
                    "refresh_token": "rt",
                    "token_type": "Bearer",
                    "expires_in": 420,
                },
            )
        )
        yield mock


async def make_client() -> Client:
    settings = make_settings()
    server = build_server(settings)
    register_all(server, settings)
    return Client(server)


async def test_list_incoming_shipments_routes_to_konshub_gateway(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(
        f"{KONSHUB_BASE_URL}/ClmKonshubWebService/KonsHubShipmentQuery/GetDashboardIncommingShipmentList"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "s1"}], total_count=1)))
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_list_konshub_incoming_shipments",
            {
                "filter_schedule_start_date": "2026-01-01T00:00:00Z",
                "filter_schedule_end_date": "2026-01-07T00:00:00Z",
            },
        )

    assert not result.is_error
    assert result.structured_content["total_count"] == 1
    assert SITE_ID.encode() in route.calls[0].request.content


async def test_count_deliveries_returns_object(mock_router: respx.MockRouter) -> None:
    mock_router.post(
        f"{KONSHUB_BASE_URL}/ClmKonshubWebService/KonsHubShipmentQuery/GetDeliveriesTabShipmentCount"
    ).mock(
        return_value=httpx.Response(
            200, json=query_success({"Incoming": 3, "Unplanned": 0, "Total": 3})
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_count_konshub_deliveries", {})

    assert not result.is_error
    # zero is not null-stripped, only an explicit null is
    assert result.structured_content == {"Incoming": 3, "Unplanned": 0, "Total": 3}


async def test_list_warehouse_zones_does_not_default_site_id(
    mock_router: respx.MockRouter,
) -> None:
    """GetKonsHubZones is scoped by warehouse_id, which has no site
    default to fall back to."""
    route = mock_router.post(
        f"{KONSHUB_BASE_URL}/ClmKonshubWebService/WareHouseQuery/GetKonsHubZones"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "z1"}], total_count=1)))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_warehouse_zones", {"warehouse_id": "wh-1"})

    assert not result.is_error
    assert b"wh-1" in route.calls[0].request.content
    assert SITE_ID.encode() not in route.calls[0].request.content


async def test_search_konshub_deliveries_business_failure_raises_tool_error(
    mock_router: respx.MockRouter,
) -> None:
    mock_router.post(
        f"{KONSHUB_BASE_URL}/ClmKonshubWebService/KonsHubShipmentQuery/SearchDeliveriesShipment"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": None,
                "IsSuccess": False,
                "StatusCode": 400,
                "ErrorMessage": "Invalid search text.",
                "PropertyName": "SearchText",
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": None,
            },
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_search_konshub_deliveries", {"search_text": ""})

    assert result.is_error
    assert "Invalid search text" in str(result.content)
