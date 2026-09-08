"""Integration tests for the meta tools (clm_whoami, clm_list_enums),
exercised through the MCP SDK's in-process Client against a real
build_server() + register_all(), with only the identity endpoint mocked.
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


def make_settings() -> Settings:
    return Settings(refresh_token="rt", identity_token_url=IDENTITY_URL)


def make_jwt() -> str:
    payload = {
        "sub": "user-1",
        "user_id": "user-1",
        "site_id": "site-1",
        "site_name": "Test Site",
        "tenant_id": "tenant-1",
        "display_name": "Test User",
        "user_name": "user@example.com",
        "email": "user@example.com",
        "role": ["admin"],
        "iat": int(time.time()),
        "exp": int(time.time() + 420),
    }
    return jwt.encode(payload, key="test-signing-key-at-least-32-bytes-long!!", algorithm="HS256")


@pytest.fixture
def mocked_identity() -> Generator[respx.MockRouter]:
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


async def test_clm_whoami_returns_identity(mocked_identity: respx.MockRouter) -> None:
    settings = make_settings()
    server = build_server(settings)
    register_all(server, settings)

    async with Client(server) as client:
        result = await client.call_tool("clm_whoami", {})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["user_id"] == "user-1"
    assert result.structured_content["site_id"] == "site-1"
    assert result.structured_content["display_name"] == "Test User"
    assert result.structured_content["roles"] == ["admin"]


async def test_clm_list_enums_lists_domain_enums(mocked_identity: respx.MockRouter) -> None:
    settings = make_settings()
    server = build_server(settings)
    register_all(server, settings)

    async with Client(server) as client:
        result = await client.call_tool("clm_list_enums", {})

    assert not result.is_error
    assert result.structured_content is not None
    entries = result.structured_content["result"]
    names = {entry["name"] for entry in entries}
    assert "ShipmentStatus" in names
    assert "LeanCardStatus" in names
    assert all(entry["service"] == "shipment" for entry in entries)


async def test_meta_tools_are_registered_and_read_only(mocked_identity: respx.MockRouter) -> None:
    settings = make_settings()
    server = build_server(settings)
    register_all(server, settings)

    async with Client(server) as client:
        tools = await client.list_tools()

    by_name = {tool.name: tool for tool in tools.tools}
    assert "clm_whoami" in by_name
    assert "clm_list_enums" in by_name
    assert by_name["clm_whoami"].annotations is not None
    assert by_name["clm_whoami"].annotations.read_only_hint is True
