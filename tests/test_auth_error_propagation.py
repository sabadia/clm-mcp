"""Regression tests: every `ClmAuthError` raised by `TokenManager` (missing
credentials, invalid credentials, identity service unreachable) must reach
the caller as a `ToolError` carrying its real message.

This is a cross-cutting concern with three independent call sites, each of
which bypasses (or wraps) `ClmApiClient` and must translate the error on its
own:
  1. `api/client.py::_fetch_body` — every curated tool, gateway, and
     generated command tool that reaches the business API.
  2. `services/common.py::resolve_site_id` — every site-scoped curated tool,
     which resolves `site_id` from JWT claims *before* any business API call.
  3. `tools/meta.py::clm_whoami` — reads claims directly, no business API
     call at all.

Without the translation at each site, the MCP SDK's own dispatch wrapper
reduces any non-`ToolError` exception to a bare "Error executing tool X",
discarding the actual (and, for a first-run user, most important) message —
e.g. "run `clm-mcp login`". This was caught by re-testing exactly that
first-run scenario (no credentials configured at all) end-to-end.
"""

from __future__ import annotations

from mcp.client.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.auth.errors import MissingCredentialsError
from clm_mcp.config import Settings
from clm_mcp.server import build_server
from clm_mcp.tools import register_all


def build_with_no_credentials() -> Client:
    """A Settings with no CLM_REFRESH_TOKEN / CLM_USERNAME+PASSWORD and no
    on-disk store — the exact first-run state that surfaced this bug.

    `credentials_path` is pointed at a path that cannot exist, regardless of
    the machine running the test having a real
    ~/.config/clm-mcp/credentials.json from local use.
    """
    settings = Settings(credentials_path="/nonexistent/credentials.json")
    server = build_server(settings)
    register_all(server, settings)
    return Client(server)


MISSING_CREDENTIALS_MESSAGE = "No CLM credentials found"


async def test_clm_whoami_reports_missing_credentials_message() -> None:
    client = build_with_no_credentials()
    async with client:
        result = await client.call_tool("clm_whoami", {})

    assert result.is_error
    assert MISSING_CREDENTIALS_MESSAGE in str(result.content)


async def test_site_scoped_tool_reports_missing_credentials_message() -> None:
    """Exercises the resolve_site_id path specifically: site_id omitted, so
    it must fetch a token to read the JWT's site_id claim before ever
    reaching api_client."""
    client = build_with_no_credentials()
    async with client:
        result = await client.call_tool(
            "clm_list_shipments",
            {
                "filter_schedule_start_date": "2020-01-01T00:00:00Z",
                "filter_schedule_end_date": "2030-01-01T00:00:00Z",
            },
        )

    assert result.is_error
    assert MISSING_CREDENTIALS_MESSAGE in str(result.content)


async def test_api_client_path_reports_missing_credentials_message() -> None:
    """Exercises the api/client.py::_fetch_body path specifically: site_id
    given explicitly, so resolve_site_id is skipped and the first token
    fetch happens inside ClmApiClient._request_with_retry."""
    client = build_with_no_credentials()
    async with client:
        result = await client.call_tool("clm_get_shipment", {"shipment_id": "s1"})

    assert result.is_error
    assert MISSING_CREDENTIALS_MESSAGE in str(result.content)


async def test_generated_command_tool_reports_missing_credentials_message() -> None:
    """Same api_client path, but through an auto-generated write tool."""
    settings = Settings(
        credentials_path="/nonexistent/credentials.json",
        enable_writes=True,
        write_tools="shipment",
    )
    server = build_server(settings)
    register_all(server, settings)

    async with Client(server) as client:
        result = await client.call_tool(
            "clm_shipment_command_discard_shipment", {"params": {"ShipmentId": "s1"}}
        )

    assert result.is_error
    assert MISSING_CREDENTIALS_MESSAGE in str(result.content)


async def test_gateway_invoke_reports_missing_credentials_message() -> None:
    client = build_with_no_credentials()
    async with client:
        result = await client.call_tool(
            "clm_invoke",
            {"operation": "ShipmentQuery/GetShipmentById", "params": {"ShipmentId": "s1"}},
        )

    assert result.is_error
    assert MISSING_CREDENTIALS_MESSAGE in str(result.content)


def test_tool_error_message_survives_from_raw_client_auth_error() -> None:
    """Unit-level guard, independent of the full MCP stack: ToolError is a
    plain Exception, so `str(ToolError(str(some_clm_auth_error)))` must
    equal the original message — a cheap check that the translation
    pattern used at all three call sites doesn't itself lose information."""
    original = MissingCredentialsError()
    wrapped = ToolError(str(original))
    assert str(wrapped) == str(original)
