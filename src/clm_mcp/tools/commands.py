"""Auto-generated write tools: one MCP tool per `*Command` operation,
registered by default — set `CLM_ENABLE_WRITES=false` to opt OUT and run
this server read-only instead.

Unlike the curated read tools (hand-written, one per common workflow — see
`tools/shipments.py` etc.) and the generic `clm_invoke` escape hatch
(`tools/gateway.py`), these tools are synthesized directly from each
Command operation's resolved JSON Schema: a pydantic model is built for its
request body, and `ToolAnnotations` are derived from the operation's name
prefix (see PLAN.md "Write-mode annotations"). That gives every write
operation its own properly-named, individually-discoverable tool without
hand-writing ~59 near-identical wrappers.

Trade-off: a deeply nested field (an array of objects, say) falls back to
permissive `dict[str, Any]` / `list[dict[str, Any]]` rather than a fully
recursive schema — the API itself still validates the real shape, and
`clm_invoke` remains available when full JSON-Schema pre-validation of a
nested body matters more than a named, typed tool.

`settings.enable_writes` defaults to `True` — every endpoint gets a tool
with zero setup. The setting still exists as an explicit opt-out for a
connection that should never be able to mutate live data at all.
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
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def _tool_name(operation: Operation) -> str:
    """`clm_<tag>_<operation>`, e.g. `clm_shipment_command_discard_shipment`.

    The tag prefix is load-bearing, not decorative: several operation path
    tails repeat across tags (`SaveShipmentWizardBookingDates` exists under
    both `ShipmentCommand` and `SmartShipmentCommand`), so the tail alone
    would collide.
    """
    path_tail = operation.name.split("/", 1)[1]
    return f"clm_{_snake_case(operation.tag)}_{_snake_case(path_tail)}"


def _derive_annotations(operation: Operation) -> ToolAnnotations:
    path_tail = operation.name.split("/", 1)[1]
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
    """Register one tool per `*Command` operation — only if writes are enabled."""
    if not settings.enable_writes:
        return

    for op_summary in _REGISTRY.list_operations():
        operation = _REGISTRY.get(op_summary.name)
        if operation is None or not operation.is_command:
            continue
        model_cls = _build_params_model(operation)
        tool_fn = _make_tool_fn(operation, model_cls)
        mcp.add_tool(
            tool_fn,
            name=_tool_name(operation),
            description=tool_fn.__doc__,
            annotations=_derive_annotations(operation),
        )
