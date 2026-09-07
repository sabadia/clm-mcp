"""Tool registration entry point.

`register_all` is the single place that wires every tool module onto a
built `MCPServer` — called from `__main__.py` after `build_server()`.
Read-only tool modules (`meta`, the curated domains, `gateway`'s list/
describe/invoke) register unconditionally. `commands` — one auto-generated
tool per `*Command` operation — registers by default too
(`settings.enable_writes` defaults to `True`); see its module docstring for
the read-only opt-out.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from clm_mcp.config import Settings
from clm_mcp.server import AppContext
from clm_mcp.tools import (
    cockpit,
    commands,
    equipment,
    gateway,
    handovers,
    incidents,
    lean_cards,
    meta,
    shipments,
)


def register_all(mcp: MCPServer[AppContext], settings: Settings) -> None:
    """Register every tool module onto `mcp`."""
    meta.register(mcp)
    shipments.register(mcp)
    incidents.register(mcp)
    equipment.register(mcp)
    lean_cards.register(mcp)
    handovers.register(mcp)
    cockpit.register(mcp)
    gateway.register(mcp)
    commands.register(mcp, settings)
