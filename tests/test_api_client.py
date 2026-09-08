"""Unit tests for ClmApiClient: request construction and the asymmetric
retry policy (401 always retried once; 429/5xx retried, with backoff, only
for *Query operations; *Command operations get exactly one attempt).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.api.client import ClmApiClient
from clm_mcp.config import Settings
from clm_mcp.spec.registry import Operation

API_BASE_URL = "https://msblocks.selisestage.com/api/business-clm-shipment"


def make_settings() -> Settings:
    return Settings(api_base_url=API_BASE_URL, refresh_token="rt")


def query_op(name: str = "ShipmentQuery/GetShipmentById", method: str = "post") -> Operation:
    return Operation(
        name=f"shipment/{name}",
        service="shipment",
        method=method,
        path=f"/ClmShipmentWebService/{name.split('/', 1)[1]}",
        tag=name.split("/", 1)[0],
        summary="A query operation.",
        parameters=(),
        request_schema={"type": "object", "properties": {}},
        response_schema=None,
    )


def command_op(name: str = "ShipmentCommand/DiscardShipment") -> Operation:
    return Operation(
        name=f"shipment/{name}",
        service="shipment",
        method="post",
        path=f"/ClmShipmentWebService/{name.split('/', 1)[1]}",
        tag=name.split("/", 1)[0],
        summary="A command operation.",
        parameters=(),
        request_schema={"type": "object", "properties": {}},
        response_schema=None,
    )


def success_body(data: Any = None) -> dict[str, Any]:
    return {
        "Data": data,
        "IsSuccess": True,
        "StatusCode": 0,
        "ErrorMessage": None,
        "PropertyName": None,
        "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "TotalCount": 0 if data is None else 1,
    }


@pytest.fixture
def token_manager() -> AsyncMock:
    tm = AsyncMock()
    tm.get_access_token.return_value = "access-token-1"
    return tm


@pytest.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


class FakeSleepRecorder:
    """Records backoff durations instead of actually sleeping, so retry
    tests run instantly."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --------------------------------------------------------------------------
# Happy path: request construction (URL, headers, verb) and envelope unwrap.
# --------------------------------------------------------------------------


async def test_post_operation_sends_bearer_and_json_body(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(200, json=success_body({"Id": "abc"}))
        )
        result = await client.call(op, {"ShipmentId": "abc"})

    assert result == {"Id": "abc"}
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer access-token-1"
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == {"ShipmentId": "abc"}


async def test_get_operation_sends_query_params(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op(name="ExternalQuery/GetShipment", method="get")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(200, json=success_body({"Id": "abc"}))
        )
        result = await client.call(op, {"shipmentId": "abc"})

    assert result == {"Id": "abc"}
    assert dict(route.calls[0].request.url.params) == {"shipmentId": "abc"}


async def test_operation_routes_to_its_own_services_gateway(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    """`operation.service` (not a single fixed base URL) drives routing —
    a konshub operation must hit the konshub gateway, and a shipment
    operation must still hit the shipment gateway, from the same client."""
    client = ClmApiClient(make_settings(), http_client, token_manager)
    konshub_op = Operation(
        name="konshub/KonsHubShipmentQuery/GetLogs",
        service="konshub",
        method="post",
        path="/ClmKonshubWebService/KonsHubShipmentQuery/GetLogs",
        tag="KonsHubShipmentQuery",
        summary="",
        parameters=(),
        request_schema={"type": "object", "properties": {}},
        response_schema=None,
    )
    shipment_op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        konshub_route = mock.post(
            "https://msblocks.selisestage.com/api/business-clm-konshub"
            "/ClmKonshubWebService/KonsHubShipmentQuery/GetLogs"
        ).mock(return_value=httpx.Response(200, json=success_body([])))
        shipment_route = mock.post(f"{API_BASE_URL}{shipment_op.path}").mock(
            return_value=httpx.Response(200, json=success_body({"Id": "abc"}))
        )
        await client.call(konshub_op, {})
        await client.call(shipment_op, {"ShipmentId": "abc"})

    assert konshub_route.called
    assert shipment_route.called


# --------------------------------------------------------------------------
# 401 -> force_refresh, retried exactly once, regardless of operation type.
# --------------------------------------------------------------------------


async def test_401_forces_refresh_and_retries_once(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = command_op()  # even a Command gets this retry — a 401 never touched business logic

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}")
        route.side_effect = [
            httpx.Response(401, json={"error": "unauthorized"}),
            httpx.Response(200, json=success_body()),
        ]
        result = await client.call(op, {})

    assert result is None
    assert route.call_count == 2
    token_manager.force_refresh.assert_awaited_once()


async def test_second_401_is_not_retried_again(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    """Only one 401-triggered retry per call — a second 401 must surface as
    a failure rather than looping forever."""
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = command_op()

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(ToolError, match="401"):
            await client.call(op, {})

    token_manager.force_refresh.assert_awaited_once()


# --------------------------------------------------------------------------
# 429 / 5xx: retried with backoff for *Query, but *Command gets one attempt.
# --------------------------------------------------------------------------


async def test_query_retries_429_then_succeeds(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager, sleep=FakeSleepRecorder())
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}")
        route.side_effect = [
            httpx.Response(429, text="rate limited"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json=success_body({"ok": True})),
        ]
        result = await client.call(op, {})

    assert result == {"ok": True}
    assert route.call_count == 3


async def test_query_exhausts_retries_and_raises(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    sleeper = FakeSleepRecorder()
    client = ClmApiClient(make_settings(), http_client, token_manager, sleep=sleeper)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE_URL}{op.path}").mock(return_value=httpx.Response(503, text="down"))
        with pytest.raises(ToolError, match="503"):
            await client.call(op, {})

    # 3 attempts total -> 2 backoff sleeps between them.
    assert len(sleeper.calls) == 2


async def test_command_does_not_retry_on_503(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    """A *Command may have already mutated state — a 5xx must fail fast,
    never be blindly retried."""
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = command_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(503, text="down")
        )
        with pytest.raises(ToolError, match="503"):
            await client.call(op, {})

    assert route.call_count == 1


async def test_command_does_not_retry_on_timeout(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = command_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}")
        route.side_effect = httpx.ReadTimeout("timed out")
        with pytest.raises(ToolError, match="timed out"):
            await client.call(op, {})

    assert route.call_count == 1


async def test_query_retries_on_timeout_then_succeeds(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager, sleep=FakeSleepRecorder())
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}")
        route.side_effect = [
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, json=success_body({"ok": True})),
        ]
        result = await client.call(op, {})

    assert result == {"ok": True}
    assert route.call_count == 2


# --------------------------------------------------------------------------
# A non-retryable 4xx (e.g. 404) fails immediately for either operation type.
# --------------------------------------------------------------------------


async def test_404_is_not_retried(http_client: httpx.AsyncClient, token_manager: AsyncMock) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(ToolError, match="404"):
            await client.call(op, {})

    assert route.call_count == 1


async def test_403_forbidden_does_not_trigger_a_token_refresh(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    """A real permissions error (distinct from 401) must fail immediately —
    it must NOT force a token refresh (the token is fine; the account just
    lacks permission) and must NOT be retried as if it were a transient
    5xx, for either operation type."""
    client = ClmApiClient(make_settings(), http_client, token_manager)

    for op in (query_op(), command_op()):
        token_manager.reset_mock()
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post(f"{API_BASE_URL}{op.path}").mock(
                return_value=httpx.Response(403, text="forbidden")
            )
            with pytest.raises(ToolError, match="403"):
                await client.call(op, {})

        assert route.call_count == 1
        token_manager.force_refresh.assert_not_awaited()


# --------------------------------------------------------------------------
# End-to-end wiring: a business-envelope failure inside HTTP 200 surfaces
# as a ToolError through the full client, not just in isolated unit tests.
# --------------------------------------------------------------------------


async def test_business_failure_in_http_200_body_raises_tool_error(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Data": None,
                    "IsSuccess": False,
                    "StatusCode": 404,
                    "ErrorMessage": "Shipment not found.",
                    "PropertyName": None,
                    "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
                    "TotalCount": 0,
                },
            )
        )
        with pytest.raises(ToolError, match="Shipment not found"):
            await client.call(op, {})


# --------------------------------------------------------------------------
# call_list: like call(), but also surfaces the envelope's TotalCount.
# --------------------------------------------------------------------------


async def test_call_list_returns_data_and_total_count(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        body = success_body([{"Id": "1"}, {"Id": "2"}])
        body["TotalCount"] = 143
        mock.post(f"{API_BASE_URL}{op.path}").mock(return_value=httpx.Response(200, json=body))
        data, total_count = await client.call_list(op, {})

    assert data == [{"Id": "1"}, {"Id": "2"}]
    assert total_count == 143


async def test_call_list_business_failure_still_raises_tool_error(
    http_client: httpx.AsyncClient, token_manager: AsyncMock
) -> None:
    client = ClmApiClient(make_settings(), http_client, token_manager)
    op = query_op()

    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{API_BASE_URL}{op.path}").mock(
            return_value=httpx.Response(
                200, json={"Data": None, "IsSuccess": False, "ErrorMessage": "Nope."}
            )
        )
        with pytest.raises(ToolError, match="Nope"):
            await client.call_list(op, {})
