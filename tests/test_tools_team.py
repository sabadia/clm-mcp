"""Integration tests for the curated team tools, exercised through the MCP
SDK's in-process Client against a real build_server() + register_all(),
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
TEAM_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-team"
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


async def test_list_teams_routes_to_team_gateway_and_defaults_site_id(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(f"{TEAM_BASE_URL}/ClmTeamWebService/ClmTeamQuery/GetTeamList").mock(
        return_value=httpx.Response(200, json=query_success([{"Id": "t1"}], total_count=1))
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_teams", {})

    assert not result.is_error
    assert result.structured_content["total_count"] == 1
    assert SITE_ID.encode() in route.calls[0].request.content


async def test_list_team_members_does_not_default_site_id(mock_router: respx.MockRouter) -> None:
    """GetTeamMembersForTeam is scoped by team_id, not SiteId — SiteIds is
    an optional multi-site filter, so no site defaulting applies here."""
    mock_router.post(f"{TEAM_BASE_URL}/ClmTeamWebService/ClmTeamQuery/GetTeamMembersForTeam").mock(
        return_value=httpx.Response(200, json=query_success([{"Id": "m1"}], total_count=1))
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_team_members", {"team_id": "team-1"})

    assert not result.is_error
    assert result.structured_content["total_count"] == 1


async def test_get_join_request_returns_object(mock_router: respx.MockRouter) -> None:
    mock_router.post(f"{TEAM_BASE_URL}/ClmTeamWebService/ClmTeamQuery/GetJoinRequestDetails").mock(
        return_value=httpx.Response(200, json=query_success({"Id": "r1", "Comment": None}))
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_get_join_request", {"request_id": "r1"})

    assert not result.is_error
    assert result.structured_content == {"Id": "r1"}  # null-stripped


async def test_get_person_info_business_failure_raises_tool_error(
    mock_router: respx.MockRouter,
) -> None:
    mock_router.post(f"{TEAM_BASE_URL}/ClmTeamWebService/ClmTeamQuery/GetExtendPersonInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": None,
                "IsSuccess": False,
                "StatusCode": 400,
                "ErrorMessage": "Person not found.",
                "PropertyName": None,
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": None,
            },
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_get_person_info", {"user_id": "u1"})

    assert result.is_error
    assert "Person not found" in str(result.content)
