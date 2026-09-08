"""Focused tests for the two independent write-related settings
(CLM_ENABLE_WRITES, CLM_WRITE_TOOLS) and how tools/commands.py::register
combines them — see config.py::Settings.write_tool_services and PLAN.md
"Approved design decisions" / "Write-tool gating".
"""

from __future__ import annotations

import pytest
from mcp.client.client import Client

from clm_mcp.config import Settings
from clm_mcp.server import build_server
from clm_mcp.spec.registry import get_registry
from clm_mcp.tools import register_all


def build(*, enable_writes: bool = True, write_tools: str = "") -> Client:
    settings = Settings(refresh_token="rt", enable_writes=enable_writes, write_tools=write_tools)
    server = build_server(settings)
    register_all(server, settings)
    return Client(server)


async def command_tool_names(client: Client) -> set[str]:
    async with client:
        tools = await client.list_tools()
    return {t.name for t in tools.tools if "_command_" in t.name}


async def test_default_settings_register_no_write_tools() -> None:
    assert await command_tool_names(build()) == set()


async def test_enable_writes_false_beats_write_tools_all() -> None:
    names = await command_tool_names(build(enable_writes=False, write_tools="all"))
    assert names == set()


async def test_write_tools_shipment_registers_only_shipment_write_tools() -> None:
    names = await command_tool_names(build(write_tools="shipment"))
    assert names
    shipment_command_ops = {
        op.name
        for op in get_registry().list_operations(service="shipment")
        if op.tag.endswith("Command")
    }
    assert len(names) == len(shipment_command_ops)


async def test_write_tools_all_registers_every_services_write_tools() -> None:
    names = await command_tool_names(build(write_tools="all"))
    all_command_ops = {
        op.name for op in get_registry().list_operations() if op.tag.endswith("Command")
    }
    assert len(names) == len(all_command_ops) == 181
    assert len(names) == len(set(names))  # no collisions across services


async def test_write_tools_comma_separated_multiple_services() -> None:
    names = await command_tool_names(build(write_tools="construction,konshub"))
    expected_ops = {
        op.name
        for op in get_registry().list_operations()
        if op.tag.endswith("Command") and op.service in ("construction", "konshub")
    }
    assert len(names) == len(expected_ops)


def test_write_tools_unknown_slug_raises_at_registration() -> None:
    settings = Settings(refresh_token="rt", write_tools="not-a-service")
    server = build_server(settings)
    with pytest.raises(ValueError, match="unknown service"):
        register_all(server, settings)
