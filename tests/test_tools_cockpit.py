"""Integration tests for the curated cockpit tools, exercised through the
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
API_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-shipment"
SITE_ID = "68BC0C11-963F-46CB-AF93-267B50ABCCAF"


def make_settings() -> Settings:
    return Settings(refresh_token="rt", identity_token_url=IDENTITY_URL, api_base_url=API_BASE_URL)


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


async def test_weekly_counts_within_window_succeeds(mock_router: respx.MockRouter) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetCockpitWeeklyShipmentCounts"
    ).mock(return_value=httpx.Response(200, json=query_success({"CombinedUpData": {}})))
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_get_cockpit_weekly_counts",
            {
                "filter_schedule_start_date": "2026-01-01T00:00:00Z",
                "filter_schedule_end_date": "2026-01-08T00:00:00Z",
            },
        )

    assert not result.is_error
    assert route.called


async def test_weekly_counts_rejects_a_window_wider_than_92_days(
    mock_router: respx.MockRouter,
) -> None:
    """Regression test: GetCockpitWeeklyShipmentCounts returns one entry per
    *day* with no size cap of its own (confirmed live: a 7-year window
    returned 2,558 daily entries totalling ~530,000 bytes — over 10x
    CLM_MAX_RESPONSE_BYTES's default, with zero truncation, since it's an
    object response, not a list one). A too-wide window must be refused
    before the HTTP call, not silently returned unbounded."""
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetCockpitWeeklyShipmentCounts"
    )
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_get_cockpit_weekly_counts",
            {
                "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                "filter_schedule_end_date": "2027-01-01T00:00:00Z",
            },
        )

    assert result.is_error
    assert "92-day limit" in str(result.content)
    assert route.call_count == 0  # never reached the HTTP call


async def test_get_weather_shapes_list_response(mock_router: respx.MockRouter) -> None:
    mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/MeteomaticsQuery/GetWeatherInfoFromRange"
    ).mock(
        return_value=httpx.Response(
            200, json=query_success([{"Date": "2026-01-01", "Temperature": 5.0}], total_count=1)
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_get_weather",
            {"start_date": "2026-01-01T00:00:00Z", "end_date": "2026-01-07T00:00:00Z"},
        )

    assert not result.is_error
    assert result.structured_content["total_count"] == 1
