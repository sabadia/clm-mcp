"""Unit tests for server.py: the MCPServer factory and its typed lifespan.

No tools are registered yet at this point in the build (see PLAN.md task
9 vs. 10-13), so these tests exercise `build_server()` and the lifespan in
isolation — full tool-call integration tests come later (task 15).
"""

from __future__ import annotations

from clm_mcp.api.client import ClmApiClient
from clm_mcp.auth.token_manager import TokenManager
from clm_mcp.config import Settings
from clm_mcp.server import SERVER_NAME, AppContext, build_server, make_lifespan


def make_settings() -> Settings:
    return Settings(refresh_token="rt", api_base_url="https://example.invalid/api")


def test_build_server_returns_configured_mcp_server() -> None:
    server = build_server(make_settings())
    assert server.name == SERVER_NAME


async def test_lifespan_yields_wired_app_context() -> None:
    settings = make_settings()
    server = build_server(settings)
    lifespan = make_lifespan(settings)

    async with lifespan(server) as ctx:
        assert isinstance(ctx, AppContext)
        assert ctx.settings is settings
        assert isinstance(ctx.token_manager, TokenManager)
        assert isinstance(ctx.api_client, ClmApiClient)
        assert not ctx.http_client.is_closed


async def test_lifespan_closes_http_client_on_exit() -> None:
    settings = make_settings()
    server = build_server(settings)
    lifespan = make_lifespan(settings)

    async with lifespan(server) as ctx:
        http_client = ctx.http_client

    assert http_client.is_closed


async def test_lifespan_falls_back_to_process_settings_when_none_given() -> None:
    server = build_server()
    lifespan = make_lifespan()  # no explicit settings -> get_settings()

    async with lifespan(server) as ctx:
        assert ctx.settings is not None
        assert ctx.settings.api_base_url  # has some default, not empty
