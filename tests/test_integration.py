"""End-to-end integration tests: a single, realistic MCP session — several
different tools called in sequence through one in-process Client, against
`build_server()` + `register_all()` with the identity and business API
mocked via respx.

The per-module test files (test_tools_meta.py, test_tools_curated.py,
test_tools_gateway.py, test_tools_commands.py) already exercise this same
Client(server) pattern per tool module. This file's job is different:
proving the pieces work together across ONE continuous session — a shared
token across multiple tool calls, a 401 mid-session transparently
recovering, and a realistic sequence spanning curated tools, the gateway,
and meta tools together.
"""

from __future__ import annotations

import time

import httpx
import jwt
import respx
from mcp.client.client import Client

from clm_mcp.config import Settings
from clm_mcp.server import build_server
from clm_mcp.tools import register_all

IDENTITY_URL = "https://clm.selisestage.com/api/identity/v25/identity/token"
API_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-shipment"
SITE_ID = "68BC0C11-963F-46CB-AF93-267B50ABCCAF"


def make_jwt(exp_offset: float = 420) -> str:
    payload = {
        "sub": "user-1",
        "user_id": "user-1",
        "site_id": SITE_ID,
        "display_name": "Integration Test User",
        "role": ["admin"],
        "iat": int(time.time()),
        "exp": int(time.time() + exp_offset),
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


def build() -> Client:
    settings = Settings(
        refresh_token="rt", identity_token_url=IDENTITY_URL, api_base_url=API_BASE_URL
    )
    server = build_server(settings)
    register_all(server, settings)
    return Client(server)


async def test_one_session_many_tools_shares_a_single_token() -> None:
    """whoami, a curated list tool, and the gateway's list/describe/invoke
    all succeed within one session, and only ONE identity request is made
    across all of them — proving the token is cached and shared, not
    re-fetched per tool call."""
    identity_calls = 0

    def identity_responder(_request: httpx.Request) -> httpx.Response:
        nonlocal identity_calls
        identity_calls += 1
        return httpx.Response(
            200,
            json={
                "access_token": make_jwt(),
                "refresh_token": "rt",
                "token_type": "Bearer",
                "expires_in": 420,
            },
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=identity_responder)
        mock.post(
            f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentsForListView"
        ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "s1"}], 1)))
        mock.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById").mock(
            return_value=httpx.Response(200, json=query_success({"Id": "s1"}))
        )

        client = build()
        async with client:
            whoami = await client.call_tool("clm_whoami", {})
            assert not whoami.is_error
            assert whoami.structured_content["display_name"] == "Integration Test User"

            listed = await client.call_tool(
                "clm_list_shipments",
                {
                    "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                    "filter_schedule_end_date": "2030-01-01T00:00:00Z",
                },
            )
            assert not listed.is_error
            assert listed.structured_content["total_count"] == 1

            ops = await client.call_tool("clm_list_operations", {"search": "getshipmentbyid"})
            assert not ops.is_error
            op_names = [o["name"] for o in ops.structured_content["result"]]
            assert op_names == ["ShipmentQuery/GetShipmentById"]

            described = await client.call_tool(
                "clm_describe_operation", {"operation": "ShipmentQuery/GetShipmentById"}
            )
            assert not described.is_error

            invoked = await client.call_tool(
                "clm_invoke",
                {"operation": "ShipmentQuery/GetShipmentById", "params": {"ShipmentId": "s1"}},
            )
            assert not invoked.is_error

    assert identity_calls == 1, "expected exactly one identity request across the whole session"


async def test_401_mid_session_recovers_transparently() -> None:
    """A tool call that hits a stale-on-the-server-side token (401) forces
    exactly one re-authentication and succeeds, without the caller seeing
    any error."""
    identity_calls = 0

    def identity_responder(_request: httpx.Request) -> httpx.Response:
        nonlocal identity_calls
        identity_calls += 1
        return httpx.Response(
            200,
            json={
                "access_token": make_jwt(),
                "refresh_token": "rt",
                "token_type": "Bearer",
                "expires_in": 420,
            },
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=identity_responder)
        route = mock.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById")
        route.side_effect = [
            httpx.Response(401, json={"error": "unauthorized"}),
            httpx.Response(200, json=query_success({"Id": "s1"})),
        ]

        client = build()
        async with client:
            result = await client.call_tool("clm_get_shipment", {"shipment_id": "s1"})

    assert not result.is_error
    assert result.structured_content == {"Id": "s1"}
    assert identity_calls == 2  # initial fetch + the forced refresh after the 401
    assert route.call_count == 2


async def test_full_tool_catalog_is_internally_consistent() -> None:
    """Every registered tool's name is unique and every write tool is
    correctly annotated — a final consistency check on the whole assembled
    server, read-only and write-enabled."""
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

        settings = Settings(
            refresh_token="rt",
            identity_token_url=IDENTITY_URL,
            api_base_url=API_BASE_URL,
            enable_writes=True,
        )
        server = build_server(settings)
        register_all(server, settings)

        async with Client(server) as client:
            tools = await client.list_tools()

    names = [t.name for t in tools.tools]
    assert len(names) == len(set(names))
    assert len(names) > 80  # curated + meta + gateway + all generated command tools

    for tool in tools.tools:
        if "_command_" in tool.name:
            assert tool.annotations is not None, f"{tool.name} is missing annotations"
