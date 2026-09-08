"""Integration tests for the gateway tools: clm_list_operations,
clm_describe_operation, clm_invoke — including the write-gate and the
Test/* exclusion enforced at the registry level.
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


def make_jwt() -> str:
    payload = {
        "sub": "user-1",
        "user_id": "user-1",
        "site_id": "site-1",
        "role": ["admin"],
        "iat": int(time.time()),
        "exp": int(time.time() + 420),
    }
    return jwt.encode(payload, key="test-signing-key-at-least-32-bytes-long!!", algorithm="HS256")


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


def build(enable_writes: bool = False) -> tuple[Client, Settings]:
    settings = Settings(
        refresh_token="rt",
        identity_token_url=IDENTITY_URL,
        api_base_url=API_BASE_URL,
        enable_writes=enable_writes,
    )
    server = build_server(settings)
    register_all(server, settings)
    return Client(server), settings


async def test_list_operations_excludes_test_tag(mock_router: respx.MockRouter) -> None:
    client, _ = build()
    async with client:
        result = await client.call_tool("clm_list_operations", {"tag": "Test"})

    assert not result.is_error
    assert result.structured_content["result"] == []


async def test_list_operations_filters_by_search(mock_router: respx.MockRouter) -> None:
    client, _ = build()
    async with client:
        result = await client.call_tool("clm_list_operations", {"search": "getshipmentbyid"})

    names = [op["name"] for op in result.structured_content["result"]]
    assert names == ["shipment/ShipmentQuery/GetShipmentById"]


async def test_describe_operation_returns_resolved_schema(mock_router: respx.MockRouter) -> None:
    client, _ = build()
    async with client:
        result = await client.call_tool(
            "clm_describe_operation", {"operation": "ShipmentQuery/GetShipmentById"}
        )

    assert not result.is_error
    schema = result.structured_content["request_schema"]
    assert set(schema["properties"]) == {"ShipmentId"}
    assert result.structured_content["is_command"] is False


async def test_describe_unknown_operation_raises_tool_error(mock_router: respx.MockRouter) -> None:
    client, _ = build()
    async with client:
        result = await client.call_tool(
            "clm_describe_operation", {"operation": "NotARealOperation"}
        )

    assert result.is_error
    assert "Unknown operation" in str(result.content)


async def test_describe_test_tag_operation_is_unreachable(mock_router: respx.MockRouter) -> None:
    """Test/GetUserData leaks super-admin credentials upstream (see
    PLAN.md) — the gateway must never be able to reach it, by name or
    otherwise."""
    client, _ = build()
    async with client:
        result = await client.call_tool("clm_describe_operation", {"operation": "Test/GetUserData"})

    assert result.is_error
    assert "Unknown operation" in str(result.content)


async def test_invoke_executes_a_query_operation(mock_router: respx.MockRouter) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": {"Id": "s1", "Comment": None},
                "IsSuccess": True,
                "StatusCode": 0,
                "ErrorMessage": None,
                "PropertyName": None,
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": 1,
            },
        )
    )
    client, _ = build()
    async with client:
        result = await client.call_tool(
            "clm_invoke",
            {"operation": "ShipmentQuery/GetShipmentById", "params": {"ShipmentId": "s1"}},
        )

    assert not result.is_error
    assert "s1" in str(result.content)  # freeform (Any return type -> no structured output)
    assert "Comment" not in str(result.content)  # strip_nulls applied


async def test_invoke_rejects_invalid_params_before_http_call(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById")
    client, _ = build()
    async with client:
        result = await client.call_tool(
            "clm_invoke",
            {
                "operation": "ShipmentQuery/GetShipmentById",
                "params": {"ShipmentId": 12345},  # wrong type: should be a string
            },
        )

    assert result.is_error
    assert "invalid params" in str(result.content)
    assert route.call_count == 0  # never reached the HTTP call


async def test_invoke_refuses_command_when_writes_disabled(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentCommand/DiscardShipment"
    )
    client, _ = build(enable_writes=False)
    async with client:
        result = await client.call_tool(
            "clm_invoke",
            {"operation": "ShipmentCommand/DiscardShipment", "params": {"ShipmentId": "s1"}},
        )

    assert result.is_error
    assert "write operation" in str(result.content)
    assert route.call_count == 0


async def test_list_operations_filters_by_service(mock_router: respx.MockRouter) -> None:
    client, _ = build()
    async with client:
        result = await client.call_tool("clm_list_operations", {"service": "konshub", "limit": 500})

    rows = result.structured_content["result"]
    assert rows
    assert all(row["service"] == "konshub" for row in rows)
    assert len(rows) == 86  # pinned in PLAN.md / test_full_coverage.py


@pytest.mark.parametrize("service", ["shipment", "construction", "team", "konshub"])
async def test_test_tag_is_unreachable_in_every_service(
    mock_router: respx.MockRouter, service: str
) -> None:
    """GetUserData leaks super-admin credentials upstream in all four
    services (see PLAN.md) — none of them may be reachable, by either the
    canonical qualified name or the unqualified alias."""
    client, _ = build()
    async with client:
        by_canonical = await client.call_tool(
            "clm_describe_operation", {"operation": f"{service}/Test/GetUserData"}
        )
        by_unqualified = await client.call_tool(
            "clm_describe_operation", {"operation": "Test/GetUserData"}
        )

    assert by_canonical.is_error
    assert "Unknown operation" in str(by_canonical.content)
    assert by_unqualified.is_error
    assert "Unknown operation" in str(by_unqualified.content)


async def test_describe_operation_accepts_canonical_qualified_name(
    mock_router: respx.MockRouter,
) -> None:
    """A newer caller can pass the fully-qualified `{service}/{tag}/{op}`
    form directly, without relying on unqualified-name uniqueness."""
    client, _ = build()
    async with client:
        result = await client.call_tool(
            "clm_describe_operation", {"operation": "shipment/ShipmentQuery/GetShipmentById"}
        )

    assert not result.is_error
    assert result.structured_content["service"] == "shipment"


async def test_invoke_allows_command_when_writes_enabled(mock_router: respx.MockRouter) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentCommand/DiscardShipment"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "RequestUri": None,
                "ExternalError": None,
                "HttpStatusCode": 200,
                "Errors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "ErrorMessages": [],
                "StatusCode": 200,
            },
        )
    )
    client, _ = build(enable_writes=True)
    async with client:
        result = await client.call_tool(
            "clm_invoke",
            {"operation": "ShipmentCommand/DiscardShipment", "params": {"ShipmentId": "s1"}},
        )

    assert not result.is_error
    assert route.call_count == 1
