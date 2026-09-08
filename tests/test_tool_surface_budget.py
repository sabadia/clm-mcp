"""The regression guard for this project's central multi-service design
decision (see PLAN.md "The tool-surface explosion" / "Approved design
decisions"): naively registering every write operation across all four CLM
services would cost ~204 tools and ~85,000 tokens of tool-definition
context on every single request. The gateway-first default (CLM_WRITE_TOOLS
unset -> zero generated write tools; clm_invoke is the write path instead)
is what keeps that from happening — this test measures the actual served
tool catalog's size and byte cost, so a future change that quietly
re-introduces the explosion (e.g. flipping CLM_WRITE_TOOLS's default, or
registering some other large tool set unconditionally) fails loudly here.
"""

from __future__ import annotations

import json

from mcp.client.client import Client

from clm_mcp.config import Settings
from clm_mcp.server import build_server
from clm_mcp.tools import register_all

# A generous but real ceiling: the default catalog was ~47 tools / ~66,000
# bytes (~16,500 tokens) when this test was written — comfortably under
# these, while the naive four-service-with-all-writes catalog would be
# ~204 tools / ~313,000 bytes. Kept as round numbers so small, legitimate
# additions (a new curated tool) don't require touching this file.
MAX_DEFAULT_TOOL_COUNT = 60
MAX_DEFAULT_TOOL_DEF_BYTES = 100_000


async def _list_tools_and_bytes(settings: Settings) -> tuple[list[str], int]:
    server = build_server(settings)
    register_all(server, settings)
    client = Client(server)
    async with client:
        tools = await client.list_tools()
    dumped = [t.model_dump(mode="json", exclude_none=True) for t in tools.tools]
    return [t.name for t in tools.tools], len(json.dumps(dumped))


async def test_default_catalog_stays_within_the_tool_surface_budget() -> None:
    """No CLM_WRITE_TOOLS opt-in: the default a brand-new connection gets."""
    names, total_bytes = await _list_tools_and_bytes(Settings(refresh_token="rt"))

    assert len(names) == len(set(names))
    assert not any("_command_" in name for name in names)
    assert len(names) <= MAX_DEFAULT_TOOL_COUNT, (
        f"default tool count grew to {len(names)} (budget: {MAX_DEFAULT_TOOL_COUNT}) — "
        "if this is a deliberate new curated tool, raise the budget deliberately; if "
        "it's write tools leaking into the default catalog, that's the regression this "
        "test exists to catch."
    )
    assert total_bytes <= MAX_DEFAULT_TOOL_DEF_BYTES, (
        f"default tool-definition payload grew to {total_bytes} bytes "
        f"(budget: {MAX_DEFAULT_TOOL_DEF_BYTES}) — see PLAN.md 'The tool-surface explosion'."
    )


async def test_write_tools_shipment_adds_exactly_the_shipment_command_count() -> None:
    baseline_names, _ = await _list_tools_and_bytes(Settings(refresh_token="rt"))
    names, _ = await _list_tools_and_bytes(Settings(refresh_token="rt", write_tools="shipment"))

    added = set(names) - set(baseline_names)
    assert all("_command_" in name for name in added)
    assert len(added) == 59  # pinned shipment Command-operation count (PLAN.md)


async def test_write_tools_all_adds_exactly_181_unique_write_tools() -> None:
    baseline_names, _ = await _list_tools_and_bytes(Settings(refresh_token="rt"))
    names, total_bytes = await _list_tools_and_bytes(
        Settings(refresh_token="rt", write_tools="all")
    )

    assert len(names) == len(set(names)), "no duplicate tool names across all four services"
    added = set(names) - set(baseline_names)
    assert len(added) == 181  # pinned total Command-operation count across all 4 services (PLAN.md)
    # The naive-everything scenario this design avoids by default — pinned
    # here as a fact (not a limit), so a future spec change that shrinks or
    # grows it is visible, without gating the default budget above on it.
    assert total_bytes > MAX_DEFAULT_TOOL_DEF_BYTES
