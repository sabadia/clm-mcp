"""Integration tests for the auto-generated write tools (tools/commands.py):
the CLM_ENABLE_WRITES gate, name collisions, verb-derived annotations, and
one end-to-end call through the MCP SDK's in-process Client.
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
from clm_mcp.spec.registry import get_registry
from clm_mcp.tools import register_all
from clm_mcp.tools.commands import _snake_case

IDENTITY_URL = "https://clm.selisestage.com/api/identity/v25/identity/token"
API_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-shipment"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GenerateShipmentPDF", "generate_shipment_pdf"),
        ("GenerateUPTimelineExcelReport", "generate_up_timeline_excel_report"),
        ("DiscardShipment", "discard_shipment"),
        ("CreateOrUpdateEquipment", "create_or_update_equipment"),
    ],
)
def test_snake_case_treats_consecutive_capitals_as_one_acronym(name: str, expected: str) -> None:
    """Regression test: a naive per-capital-letter split turned `PDF`/`UP` into
    `p_d_f`/`u_p`, producing wrong, undiscoverable tool names — found live when
    `GenerateShipmentPDF`'s generated tool name didn't match what was called."""
    assert _snake_case(name) == expected


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


def build(enable_writes: bool) -> Client:
    settings = Settings(
        refresh_token="rt",
        identity_token_url=IDENTITY_URL,
        api_base_url=API_BASE_URL,
        enable_writes=enable_writes,
    )
    server = build_server(settings)
    register_all(server, settings)
    return Client(server)


async def test_no_command_tools_when_writes_disabled(mock_router: respx.MockRouter) -> None:
    client = build(enable_writes=False)
    async with client:
        tools = await client.list_tools()

    generated = [t for t in tools.tools if "_command_" in t.name]
    assert generated == []


async def test_one_tool_per_command_operation_when_writes_enabled(
    mock_router: respx.MockRouter,
) -> None:
    client = build(enable_writes=True)
    async with client:
        tools = await client.list_tools()

    names = [t.name for t in tools.tools]
    assert len(names) == len(set(names)), "generated tool names must be unique"

    command_op_count = sum(
        1 for op in get_registry().list_operations() if op.tag.endswith("Command")
    )
    generated = [t for t in tools.tools if "_command_" in t.name]
    assert len(generated) == command_op_count


async def test_annotations_derived_from_verb(mock_router: respx.MockRouter) -> None:
    client = build(enable_writes=True)
    async with client:
        tools = await client.list_tools()
    by_name = {t.name: t for t in tools.tools}

    create = by_name["clm_incident_command_create_incident"].annotations
    assert create is not None
    assert create.destructive_hint is False

    delete = by_name["clm_incident_command_delete_incident"].annotations
    assert delete is not None
    assert delete.destructive_hint is True

    update = by_name["clm_incident_command_update_incident"].annotations
    assert update is not None
    assert update.destructive_hint is False
    assert update.idempotent_hint is True

    discard = by_name["clm_shipment_command_discard_shipment"].annotations
    assert discard is not None
    assert discard.destructive_hint is True


async def test_generated_tool_executes_end_to_end(mock_router: respx.MockRouter) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentCommand/DiscardShipment").mock(
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
    client = build(enable_writes=True)
    async with client:
        result = await client.call_tool(
            "clm_shipment_command_discard_shipment", {"params": {"ShipmentId": "s1"}}
        )

    assert not result.is_error
    assert result.structured_content == {
        "HttpStatusCode": 200,
        "StatusCode": 200,
        "Errors": {"IsValid": True, "Errors": []},
        "ErrorMessages": [],
    }


async def test_generated_tool_business_failure_raises_tool_error(
    mock_router: respx.MockRouter,
) -> None:
    mock_router.post(f"{API_BASE_URL}/ClmShipmentWebService/ShipmentCommand/DiscardShipment").mock(
        return_value=httpx.Response(
            200,
            json={
                "RequestUri": None,
                "ExternalError": None,
                "HttpStatusCode": 400,
                "Errors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                "ErrorMessages": ["Shipment already discarded."],
                "StatusCode": 400,
            },
        )
    )
    client = build(enable_writes=True)
    async with client:
        result = await client.call_tool(
            "clm_shipment_command_discard_shipment", {"params": {"ShipmentId": "s1"}}
        )

    assert result.is_error
    assert "already discarded" in str(result.content)


async def test_generated_tool_with_datetime_field_serializes_correctly(
    mock_router: respx.MockRouter,
) -> None:
    """Regression test: a Command operation with a `date-time` field (e.g.
    UpsertAdhocShipment's ScheduleDate) must not crash with an unhandled
    'Object of type datetime is not JSON serializable' error — the
    synthesized params model dumps in JSON mode specifically to avoid this
    (see tools/commands.py's `_fn`).
    """
    route = mock_router.post(
        f"{API_BASE_URL}/ClmShipmentWebService/ShipmentCommand/UpsertAdhocShipment"
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
    client = build(enable_writes=True)
    async with client:
        result = await client.call_tool(
            "clm_shipment_command_upsert_adhoc_shipment",
            {"params": {"ScheduleDate": "2026-01-01T00:00:00Z", "SiteId": "site-1"}},
        )

    assert not result.is_error, result.content
    sent_body = route.calls[0].request.content.decode()
    assert "2026-01-01" in sent_body  # serialized as an ISO string, not a repr
