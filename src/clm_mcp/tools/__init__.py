"""Tool registration entry point.

`register_all` is the single place that wires every tool module onto a
built `MCPServer` — called from `__main__.py` after `build_server()`.
Read-only tool modules (`meta`, the curated domains, `gateway`'s list/
describe/invoke) register unconditionally. `commands` — one auto-generated
tool per `*Command` operation, per service opted into `CLM_WRITE_TOOLS` —
is empty by default; see its module docstring and `config.py`'s
`write_tool_services`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from clm_mcp.config import Settings
from clm_mcp.server import AppContext
from clm_mcp.tools import (
    cockpit,
    commands,
    construction,
    equipment,
    gateway,
    handovers,
    incidents,
    konshub,
    lean_cards,
    meta,
    shipments,
    team,
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
    construction.register(mcp)
    team.register(mcp)
    konshub.register(mcp)
    gateway.register(mcp)
    commands.register(mcp, settings)
