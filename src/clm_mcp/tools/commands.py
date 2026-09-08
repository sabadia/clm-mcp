"""Auto-generated write tools: one MCP tool per `*Command` operation, for
each service opted in via `CLM_WRITE_TOOLS` — empty (none) by default.

Unlike the curated read tools (hand-written, one per common workflow — see
`tools/shipments.py` etc.) and the generic `clm_invoke` escape hatch
(`tools/gateway.py`), these tools are synthesized directly from each
Command operation's resolved JSON Schema: a pydantic model is built for its
request body, and `ToolAnnotations` are derived from the operation's name
prefix (see PLAN.md "Write-mode annotations"). That gives every write
operation its own properly-named, individually-discoverable tool without
hand-writing ~181 near-identical wrappers.

Trade-off: a deeply nested field (an array of objects, say) falls back to
permissive `dict[str, Any]` / `list[dict[str, Any]]` rather than a fully
recursive schema — the API itself still validates the real shape, and
`clm_invoke` remains available when full JSON-Schema pre-validation of a
nested body matters more than a named, typed tool.

`settings.write_tools` (`CLM_WRITE_TOOLS`) defaults to empty — no generated
write tools at all. With all four services loaded, generating every one of
the 181 write operations costs ~78,000 tokens of tool-definition context on
every single request (see PLAN.md "The tool-surface explosion"), which is
why `clm_invoke` — always available, schema-validated, and gated by
`settings.enable_writes` below — is the default write path instead. Set
`CLM_WRITE_TOOLS=shipment` (or a comma-separated list, or the literal
"all") to opt specific services back into named per-operation write tools.

`settings.enable_writes` (`CLM_ENABLE_WRITES`, default `True`) is a
separate, independent gate: it controls whether a write may **execute at
all** — through `clm_invoke` or a generated tool — regardless of
`CLM_WRITE_TOOLS`. Setting it `false` makes this server fully read-only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, create_model

from clm_mcp.config import Settings
from clm_mcp.server import AppContext
from clm_mcp.services.common import call_object
from clm_mcp.spec.registry import Operation, get_registry

_REGISTRY = get_registry()

_JSON_SCALAR_TYPES: dict[str, type] = {
    "integer": int,
    "number": float,
    "boolean": bool,
}

# Per PLAN.md "Write-mode annotations": derived from the operation's leading
# verb. A verb not covered by that explicit rule (Send/Generate/Complete/
# Change/Calculate/Upload/...) gets the conservative fallback below —
# annotations are hints, not an enforcement mechanism, so over-warning on an
# unrecognized verb is the safe default, under-warning is not.
_ANNOTATIONS_BY_VERB: dict[str, ToolAnnotations] = {
    "Get": ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    "Create": ToolAnnotations(destructive_hint=False),
    "Update": ToolAnnotations(destructive_hint=False, idempotent_hint=True),
    "Save": ToolAnnotations(destructive_hint=False, idempotent_hint=True),
    "Upsert": ToolAnnotations(destructive_hint=False, idempotent_hint=True),
    "Delete": ToolAnnotations(destructive_hint=True),
    "Discard": ToolAnnotations(destructive_hint=True),
}
_FALLBACK_ANNOTATIONS = ToolAnnotations(destructive_hint=True, idempotent_hint=False)

# Some specs mix internal-capital compounds ("KonsHub", "WareHouse") into
# names that read better as one word: without this, `_snake_case` treats the
# lowercase-to-uppercase boundary as a genuine word split, producing
# `clm_kons_hub_shipment_command_...` / `clm_ware_house_command_...` instead
# of the intended `clm_konshub_shipment_command_...` /
# `clm_warehouse_command_...`. Applied before `_snake_case`'s own boundary
# rules, on both tags and path tails.
_WORD_OVERRIDES: dict[str, str] = {"KonsHub": "Konshub", "WareHouse": "Warehouse"}


def _apply_word_overrides(name: str) -> str:
    for original, replacement in _WORD_OVERRIDES.items():
        name = name.replace(original, replacement)
    return name


def _snake_case(name: str) -> str:
    """PascalCase -> snake_case, treating a run of capitals as one acronym.

    A naive `(?=[A-Z])` boundary insertion splits every capital letter, so
    `GenerateShipmentPDF` becomes `generate_shipment_p_d_f` instead of
    `generate_shipment_pdf`. This inserts underscores only at genuine word
    boundaries: before a capital that starts a new lowercase word (acronym
    followed by a word, e.g. `UPTimeline` -> `UP_Timeline`), and between a
    lowercase/digit and a following capital (e.g. `ShipmentPDF` ->
    `Shipment_PDF`).
    """
    name = _apply_word_overrides(name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def _tool_name(operation: Operation) -> str:
    """`clm_<tag>_<operation>`, e.g. `clm_shipment_command_discard_shipment`.

    The tag prefix is load-bearing, not decorative: several operation path
    tails repeat across tags (`SaveShipmentWizardBookingDates` exists under
    both `ShipmentCommand` and `SmartShipmentCommand`), so the tail alone
    would collide. Derived from `operation.path` (not `operation.name`,
    which is `{service}/{Tag}/{PathTail}` and would leak a literal "/" into
    the tool name) — no service segment: measured unique across all 181
    write operations in all four services without one (see PLAN.md).
    """
    path_tail = operation.path.rsplit("/", 1)[-1]
    return f"clm_{_snake_case(operation.tag)}_{_snake_case(path_tail)}"


def _derive_annotations(operation: Operation) -> ToolAnnotations:
    path_tail = operation.path.rsplit("/", 1)[-1]
    verb_match = re.match(r"[A-Z][a-z]*", path_tail)
    verb = verb_match.group(0) if verb_match else ""
    return _ANNOTATIONS_BY_VERB.get(verb, _FALLBACK_ANNOTATIONS)


def _python_type_for_schema(schema: dict[str, Any]) -> Any:
    """Map one resolved JSON Schema property to a Python type for a
    synthesized pydantic field. See module docstring for the nested-shape
    fallback trade-off."""
    schema_type = schema.get("type")
    if schema_type == "string":
        return datetime if schema.get("format") in ("date-time", "date") else str
    if schema_type in _JSON_SCALAR_TYPES:
        return _JSON_SCALAR_TYPES[schema_type]
    if schema_type == "array":
        item_schema = schema.get("items")
        item_type = item_schema.get("type") if isinstance(item_schema, dict) else None
        if item_type in _JSON_SCALAR_TYPES:
            return list[_JSON_SCALAR_TYPES[item_type]]  # type: ignore[valid-type]
        if item_type == "string":
            return list[str]
        return list[dict[str, Any]]
    if schema_type == "object":
        return dict[str, Any]
    return Any


def _build_params_model(operation: Operation) -> type[BaseModel]:
    properties = (operation.request_schema or {}).get("properties", {})
    fields: dict[str, tuple[Any, Any]] = {}
    for prop_name, prop_schema in properties.items():
        python_type = _python_type_for_schema(prop_schema)
        enum_title = prop_schema.get("title") if "enum" in prop_schema else None
        description = f"See clm_list_enums for {enum_title} values." if enum_title else None
        fields[prop_name] = (
            python_type | None,
            Field(default=None, description=description),
        )
    model_name = operation.name.replace("/", "_") + "Params"
    # `create_model`'s overloads don't match a **dict-spread of dynamically
    # typed field tuples — this is inherently a runtime construction mypy
    # can't verify statically.
    model: type[BaseModel] = create_model(model_name, __module__=__name__, **fields)  # type: ignore[call-overload]
    return model


def _make_tool_fn(operation: Operation, model_cls: type[BaseModel]) -> Any:
    async def _fn(ctx: Context[AppContext], params: Any) -> dict[str, Any]:
        app = ctx.request_context.lifespan_context
        # mode="json" is required, not cosmetic: a `datetime`-typed field
        # (e.g. UpsertAdhocShipment's ScheduleDate) stays a raw `datetime`
        # object under the default mode="python", and httpx's `json=`
        # cannot serialize that — it would crash every call with a
        # date-time field instead of raising a clean ToolError.
        body = params.model_dump(exclude_none=True, mode="json")
        return await call_object(app, operation.name, body)

    _fn.__annotations__ = {
        "ctx": Context[AppContext],
        "params": model_cls,
        "return": dict[str, Any],
    }
    _fn.__name__ = _tool_name(operation)
    _fn.__doc__ = operation.summary or f"Execute {operation.name} (a write operation)."
    return _fn


def register(mcp: MCPServer[AppContext], settings: Settings) -> None:
    """Register one tool per `*Command` operation, for each service in
    `settings.write_tool_services()` — empty by default, so this is a no-op
    unless `CLM_WRITE_TOOLS` opts a service in. Also a no-op whenever writes
    can't execute at all (`CLM_ENABLE_WRITES=false`), regardless of
    `CLM_WRITE_TOOLS` — no point registering a tool that would always refuse.
    """
    if not settings.enable_writes:
        return
    enabled_services = settings.write_tool_services()
    if not enabled_services:
        return

    seen_tool_names: dict[str, str] = {}
    for op_summary in _REGISTRY.list_operations():
        operation = _REGISTRY.get(op_summary.name)
        if operation is None or not operation.is_command:
            continue
        if operation.service not in enabled_services:
            continue

        tool_name = _tool_name(operation)
        if tool_name in seen_tool_names:
            # Measured unique across all 181 write operations in all four
            # services during design (see PLAN.md) — a collision here means
            # a future spec change broke that, and a silently-overwritten
            # tool (the MCP SDK's add_tool behavior) is worse than a loud
            # failure at startup.
            raise ValueError(
                f"Generated write-tool name collision: {tool_name!r} is derived from both "
                f"{seen_tool_names[tool_name]!r} and {operation.name!r} — "
                "tools/commands.py needs a richer naming scheme."
            )
        seen_tool_names[tool_name] = operation.name

        model_cls = _build_params_model(operation)
        tool_fn = _make_tool_fn(operation, model_cls)
        mcp.add_tool(
            tool_fn,
            name=tool_name,
            description=tool_fn.__doc__,
            annotations=_derive_annotations(operation),
        )
