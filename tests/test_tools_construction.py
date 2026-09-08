"""Integration tests for the curated construction tools, exercised through
the MCP SDK's in-process Client against a real build_server() +
register_all(), with the identity and business API endpoints mocked via
respx. Follows the same representative-coverage approach as
test_tools_curated.py: object vs. list shaping, SiteId defaulting, and the
GET operations' lower-camelCase query parameter names.
"""

from __future__ import annotations

import json
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
CONSTRUCTION_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-construction"
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


async def test_get_material_routes_to_construction_gateway(mock_router: respx.MockRouter) -> None:
    """Confirms construction operations hit their own gateway
    (business-clm-construction), not the shipment default."""
    mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetMaterialById"
    ).mock(return_value=httpx.Response(200, json=query_success({"Id": "m1", "Name": "Cement"})))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_get_material", {"item_id": "m1"})

    assert not result.is_error
    assert result.structured_content == {"Id": "m1", "Name": "Cement"}


async def test_list_materials_defaults_site_id_from_jwt(mock_router: respx.MockRouter) -> None:
    mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetMaterialList"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "m1"}], total_count=1)))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_materials", {})

    assert not result.is_error
    assert result.structured_content["total_count"] == 1


async def test_get_material_usage_succeeds_despite_bare_issuccess_false(
    mock_router: respx.MockRouter,
) -> None:
    """Regression test: GetMaterialUsagesById always reports IsSuccess=false
    even on a genuine successful lookup (confirmed live via curl, including
    with a payload the user supplied independently) — api/envelope.py
    treats a populated Data as success for this one operation specifically."""
    mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetMaterialUsagesById"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": {
                    "MaterialId": "d45f6d25-6686-4176-b757-77e919047689",
                    "MaterialName": "10",
                    "OpenShipmentCount": 1,
                    "ApprovedShipmentCount": 5,
                    "CompletedShipmentCount": 1,
                    "TotalShipmentCount": 7,
                },
                "IsSuccess": False,
                "StatusCode": 0,
                "ErrorMessage": None,
                "PropertyName": None,
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": 0,
            },
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_get_material_usage", {"material_id": "d45f6d25-6686-4176-b757-77e919047689"}
        )

    assert not result.is_error
    assert result.structured_content["TotalShipmentCount"] == 7


async def test_get_material_usage_nonexistent_material_still_raises(
    mock_router: respx.MockRouter,
) -> None:
    """A genuinely nonexistent MaterialId returns Data: null on this same
    operation (confirmed live) — the override must not swallow that."""
    mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetMaterialUsagesById"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": None,
                "IsSuccess": False,
                "StatusCode": 0,
                "ErrorMessage": None,
                "PropertyName": None,
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": 0,
            },
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool(
            "clm_get_material_usage", {"material_id": "00000000-0000-0000-0000-000000000000"}
        )

    assert result.is_error


async def test_list_materials_defaults_to_page_number_one(mock_router: respx.MockRouter) -> None:
    """Regression test: GetMaterialList computes `skip = (PageNumber - 1) *
    PageSize` internally and rejects `PageNumber=0` with a 500 — confirmed
    live against a site with real materials (on an empty/corrupted site the
    same bad default instead surfaced as an unrelated-looking
    NullReferenceException, which is what made this easy to miss at first)."""
    route = mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetMaterialList"
    ).mock(return_value=httpx.Response(200, json=query_success([], total_count=0)))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_materials", {})

    assert not result.is_error
    sent = json.loads(route.calls[0].request.content)
    assert sent["PageNumber"] == 1
    assert SITE_ID.encode() in route.calls[0].request.content


async def test_list_zones_sends_lower_camel_case_query_params(
    mock_router: respx.MockRouter,
) -> None:
    """GetZones is a GET operation: query param names are the spec's own
    `siteId`/`isActive`, not the PascalCase used by POST request bodies."""
    route = mock_router.get(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetZones"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "z1"}], total_count=1)))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_list_zones", {"is_active": True})

    assert not result.is_error
    params = dict(route.calls[0].request.url.params)
    assert params == {"siteId": SITE_ID, "isActive": "true"}


async def test_get_zone_uses_zone_id_query_param(mock_router: respx.MockRouter) -> None:
    route = mock_router.get(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/ConstructionManagementQuery/GetZoneById"
    ).mock(return_value=httpx.Response(200, json=query_success({"Id": "z1"})))
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_get_zone", {"zone_id": "z1"})

    assert not result.is_error
    assert dict(route.calls[0].request.url.params) == {"zoneId": "z1"}


async def test_search_wiki_shapes_list_response(mock_router: respx.MockRouter) -> None:
    mock_router.post(
        f"{CONSTRUCTION_BASE_URL}/ClmConstructionWebService/WikiQuery/SearchWikiContentByKeyword"
    ).mock(
        return_value=httpx.Response(
            200, json=query_success([{"PageId": "p1", "Title": "Onboarding"}], total_count=1)
        )
    )
    client = await make_client()
    async with client:
        result = await client.call_tool("clm_search_wiki", {"search_text": "onboarding"})

    assert not result.is_error
    assert result.structured_content["data"] == [{"PageId": "p1", "Title": "Onboarding"}]
