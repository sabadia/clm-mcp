"""Builds the MCPServer instance and its typed lifespan.

The lifespan owns the process-wide singletons every tool call needs: a
single shared `httpx.AsyncClient` (connection reuse matters — with a
7-minute access-token TTL, a fresh connection per call would repeat the TLS
handshake far more often than necessary), the `TokenManager` built around
it, and the `ClmApiClient` that ties auth injection, retry policy, and
envelope handling together for every operation call.

`build_server()` only constructs the bare server and its lifespan — tool
registration is a separate step (`clm_mcp.tools.register_all`), called by
`__main__.py`, so this module has no dependency on the tool set and can be
tested in isolation before any tool exists.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server.mcpserver import MCPServer

from clm_mcp.api.client import ClmApiClient
from clm_mcp.auth.token_manager import TokenManager
from clm_mcp.config import Settings, get_settings
from clm_mcp.logging import get_logger

logger = get_logger(__name__)

SERVER_NAME = "clm-mcp"
SERVER_INSTRUCTIONS = (
    "Tools for the SELISE CLM Shipment API: shipments, incidents, site equipment, lean "
    "cards (working packages), material handovers, and timelines. Query tools are "
    "read-only and safe to call freely; write tools (one per write operation, e.g. "
    "clm_shipment_command_discard_shipment) mutate live data and are annotated accordingly — "
    "treat destructive-hinted tools with care regardless of what the name sounds like. Write "
    "tools are present by default; set CLM_ENABLE_WRITES=false to run this server read-only "
    "instead. Call clm_whoami first to see the authenticated user, site, and role. For anything "
    "not covered by a named tool, use clm_list_operations / clm_describe_operation / clm_invoke "
    "to reach the rest of the API surface — every operation is reachable that way even without "
    "a dedicated tool."
)


@dataclass(slots=True)
class AppContext:
    """Everything a tool needs, reached via `ctx.request_context.lifespan_context`."""

    settings: Settings
    http_client: httpx.AsyncClient
    token_manager: TokenManager
    api_client: ClmApiClient


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=settings.api_connect_timeout_seconds,
        read=settings.api_read_timeout_seconds,
        write=settings.api_read_timeout_seconds,
        pool=settings.api_connect_timeout_seconds,
    )
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    return httpx.AsyncClient(timeout=timeout, limits=limits)


def make_lifespan(
    settings: Settings | None = None,
) -> Callable[[MCPServer[AppContext]], AbstractAsyncContextManager[AppContext]]:
    """Build a lifespan callable bound to `settings` (or the process-wide
    default). A factory rather than a bare function so tests can inject a
    non-default `Settings` without mutating global state.
    """

    @asynccontextmanager
    async def lifespan(server: MCPServer[AppContext]) -> AsyncGenerator[AppContext]:
        del server  # required by the lifespan signature; unused here
        resolved_settings = settings or get_settings()
        async with _build_http_client(resolved_settings) as http_client:
            token_manager = TokenManager(resolved_settings, http_client)
            api_client = ClmApiClient(resolved_settings, http_client, token_manager)
            logger.info(
                "server.lifespan_started",
                api_base_url=resolved_settings.api_base_url,
                enable_writes=resolved_settings.enable_writes,
            )
            try:
                yield AppContext(
                    settings=resolved_settings,
                    http_client=http_client,
                    token_manager=token_manager,
                    api_client=api_client,
                )
            finally:
                logger.info("server.lifespan_stopped")

    return lifespan


def build_server(settings: Settings | None = None) -> MCPServer[AppContext]:
    """Construct the MCPServer with its lifespan wired up.

    Tool registration is not done here — see `clm_mcp.tools.register_all`.
    """
    return MCPServer(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=make_lifespan(settings),
    )
