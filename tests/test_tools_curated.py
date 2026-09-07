"""Integration tests for the curated domain tools, exercised through the
MCP SDK's in-process Client against a real build_server() + register_all(),
with the identity and business API endpoints mocked via respx.

Not every one of the ~18 curated tools gets its own test here — they all
share the same plumbing (services/common.py's call_list/call_object/
resolve_site_id), so a representative tool per behavior is what's worth
locking in: object vs. list shaping, SiteId defaulting from JWT claims,
enum name resolution, GET-vs-POST construction, and a business failure
(the exact NullReferenceException shape observed live against the staging
API's LeanCardQuery/GetWorkingPackages) surfacing as a ToolError.
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


async def make_client_ctx() -> tuple[Client, Settings]:
    settings = make_settings()
    server = build_server(settings)
    register_all(server, settings)
    return Client(server), settings


# --------------------------------------------------------------------------
# Object-shaped tool: clm_get_shipment
# --------------------------------------------------------------------------


async def test_get_shipment_returns_null_stripped_object(mock_router: respx.MockRouter) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById").mock(
        return_value=httpx.Response(
            200, json=query_success({"Id": "s1", "Comment": None, "Status": 2})
        )
    )
    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool("clm_get_shipment", {"shipment_id": "s1"})

    assert not result.is_error
    assert result.structured_content == {"Id": "s1", "Status": 2}


# --------------------------------------------------------------------------
# List-shaped tool + SiteId defaulting from JWT claims
# --------------------------------------------------------------------------


async def test_list_shipments_defaults_site_id_from_claims(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentsForListView"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Id": "s1"}], total_count=99)))

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_shipments",
            {
                "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                "filter_schedule_end_date": "2030-01-01T00:00:00Z",
                # site_id deliberately omitted
            },
        )

    assert not result.is_error
    assert result.structured_content["data"] == [{"Id": "s1"}]
    assert result.structured_content["total_count"] == 99
    sent_body = route.calls[0].request.content
    assert SITE_ID.encode() in sent_body  # the JWT's site_id claim was used


# --------------------------------------------------------------------------
# Enum name resolution: clm_list_working_packages accepts a placeholder name
# --------------------------------------------------------------------------


async def test_list_working_packages_resolves_enum_name_to_int(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/LeanCardQuery/GetWorkingPackages"
    ).mock(return_value=httpx.Response(200, json=query_success([])))

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_working_packages", {"status": "UNKNOWN_1", "site_id": SITE_ID}
        )

    assert not result.is_error
    sent = json.loads(route.calls[0].request.content)
    assert sent["Status"] == 1  # resolved from the name to its int value


async def test_list_working_packages_bad_enum_name_reports_helpful_tool_error(
    mock_router: respx.MockRouter,
) -> None:
    """Regression test: `resolve_enum_param` must turn an invalid enum name
    into a `ToolError` carrying its "valid names are..." message — a plain
    `ValueError` here would be swallowed by the MCP SDK into a generic,
    useless "Error executing tool" message with no indication of what was
    wrong or how to fix it.
    """
    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_working_packages", {"status": "NOT_A_REAL_STATUS", "site_id": SITE_ID}
        )

    assert result.is_error
    assert "Unknown LeanCardStatus member" in str(result.content)
    assert "UNKNOWN_0" in str(result.content)  # the helpful list of valid names


# --------------------------------------------------------------------------
# GET-method tool: clm_get_shipment_event_logs
# --------------------------------------------------------------------------


async def test_get_shipment_event_logs_sends_get_with_query_param(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.get(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetEventLogs"
    ).mock(return_value=httpx.Response(200, json=query_success([{"Message": "created"}])))

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool("clm_get_shipment_event_logs", {"item_id": "s1"})

    assert not result.is_error
    assert result.structured_content["data"] == [{"Message": "created"}]
    assert dict(route.calls[0].request.url.params) == {"itemId": "s1"}


# --------------------------------------------------------------------------
# Business failure end-to-end: the exact shape observed live against
# staging's LeanCardQuery/GetWorkingPackages (an upstream NullReferenceException
# surfaced through the envelope) must reach the model as a ToolError.
# --------------------------------------------------------------------------


async def test_business_failure_surfaces_as_tool_error(mock_router: respx.MockRouter) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/LeanCardQuery/GetWorkingPackages").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": None,
                "IsSuccess": False,
                "StatusCode": 500,
                "ErrorMessage": "Errors: Object reference not set to an instance of an object.",
                "PropertyName": None,
                "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "TotalCount": 0,
            },
        )
    )

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_working_packages", {"status": 0, "site_id": SITE_ID}
        )

    assert result.is_error
    assert "Object reference not set" in str(result.content)


# --------------------------------------------------------------------------
# Count/object tool with a nested bucket breakdown
# --------------------------------------------------------------------------


async def test_count_shipments_returns_bucket_object(mock_router: respx.MockRouter) -> None:
    mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentsCountForListView"
    ).mock(
        return_value=httpx.Response(
            200,
            json=query_success(
                {
                    "OpenShipmentCount": 3,
                    "ApprovedShipmentCount": 1,
                    "CompletedShipmentCount": 0,
                    "CancelledShipmentCount": 0,
                    "TotalShipmentCount": 4,
                }
            ),
        )
    )

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_count_shipments",
            {
                "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                "filter_schedule_end_date": "2030-01-01T00:00:00Z",
                "site_id": SITE_ID,
            },
        )

    assert not result.is_error
    assert result.structured_content["TotalShipmentCount"] == 4


# --------------------------------------------------------------------------
# Regression: unset optional filters must be OMITTED from the request body,
# not sent as an explicit JSON null. Verified live: staging's
# GetShipmentsForListView returns a 500 ("Value cannot be null.
# (Parameter 'key')") when OrderByField is an explicit null, but succeeds
# when the key is simply absent.
# --------------------------------------------------------------------------


async def test_list_shipments_omits_unset_optional_fields_from_request_body(
    mock_router: respx.MockRouter,
) -> None:
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentsForListView"
    ).mock(return_value=httpx.Response(200, json=query_success([])))

    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_shipments",
            {
                "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                "filter_schedule_end_date": "2030-01-01T00:00:00Z",
                "site_id": SITE_ID,
                # order_by_field and status deliberately omitted
            },
        )

    assert not result.is_error
    sent = json.loads(route.calls[0].request.content)
    assert "OrderByField" not in sent
    assert "Status" not in sent


# --------------------------------------------------------------------------
# Regression: a Get-by-id operation that reports success with a null Data
# (observed live for GetShipmentById on a nonexistent id) must be reported
# as a clear "not found", not an internal type-mismatch-sounding error.
# --------------------------------------------------------------------------


async def test_get_shipment_null_data_on_success_reports_not_found(
    mock_router: respx.MockRouter,
) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentById").mock(
        return_value=httpx.Response(200, json=query_success(None, total_count=1))
    )
    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool("clm_get_shipment", {"shipment_id": "nonexistent"})

    assert result.is_error
    assert "no result found" in str(result.content)


# --------------------------------------------------------------------------
# Regression: ValidationErrors.Errors entries are FluentValidation-style
# objects ({PropertyName, ErrorMessage, ErrorCode, ...}), not plain strings
# — the readable ErrorMessage must be extracted, not the object's Python
# repr (observed live for GetShipmentComments on a nonexistent shipment).
# --------------------------------------------------------------------------


async def test_validation_error_object_is_formatted_readably(
    mock_router: respx.MockRouter,
) -> None:
    mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentQuery/GetShipmentComments"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": None,
                "IsSuccess": False,
                "StatusCode": 0,
                "ErrorMessage": None,
                "PropertyName": None,
                "ValidationErrors": {
                    "IsValid": False,
                    "Errors": [
                        {
                            "PropertyName": "",
                            "ErrorMessage": "The shipment does not exist in the system",
                            "ErrorCode": "SHIPMENT_DOES_NOT_EXISTS",
                        }
                    ],
                    "RuleSetsExecuted": ["default"],
                },
                "TotalCount": 0,
            },
        )
    )
    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool("clm_get_shipment_comments", {"shipment_id": "nonexistent"})

    assert result.is_error
    content = str(result.content)
    assert "The shipment does not exist in the system" in content
    assert "ErrorCode" not in content  # no raw dict repr leaking through


# --------------------------------------------------------------------------
# Regression: `status` is a required, non-empty string for
# clm_list_material_handovers — verified live that the upstream API rejects
# both an omitted and a null/empty Status with "'Status' must not be empty."
# The MCP tool schema must mark it required, not silently default to None.
# --------------------------------------------------------------------------


async def test_list_material_handovers_requires_status(mock_router: respx.MockRouter) -> None:
    client, _ = await make_client_ctx()
    async with client:
        result = await client.call_tool(
            "clm_list_material_handovers",
            {
                "start_date": "2020-01-01T00:00:00Z",
                "end_date": "2030-01-01T00:00:00Z",
                "site_id": SITE_ID,
                # status deliberately omitted
            },
        )

    assert result.is_error
    assert "status" in str(result.content).lower()
