"""The full-coverage gateway: list / describe / invoke any non-excluded
operation in the spec.

The ~18 curated tools (see `tools/shipments.py` etc.) cover the common
workflows with typed parameters and shaped output. These three tools reach
everything else — roughly 100 more operations — at the cost of the model
needing to discover and validate its own request shape. `clm_invoke`
validates `params` against the operation's resolved JSON Schema *before*
making the HTTP call, so a malformed request fails fast with a clear
message instead of an opaque API error.

`Test/*` operations are unreachable here too — the registry excludes them
entirely (see `spec/registry.py`), so `clm_list_operations` never lists
them and `clm_invoke` gets a plain "unknown operation" for one. `*Command`
operations are listed, described, and executable through `clm_invoke` the
same as any query — writes are on by default (see `tools/commands.py`) —
unless the server was explicitly started with `CLM_ENABLE_WRITES=false`.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from clm_mcp.server import AppContext
from clm_mcp.spec.registry import Operation, get_registry
from clm_mcp.spec.shaping import strip_nulls

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
# clm_invoke's own annotations are open-world/non-idempotent by default —
# it can execute a *Command under the hood, so it must not claim otherwise.
_GATEWAY_INVOKE = ToolAnnotations(read_only_hint=False, idempotent_hint=False)

_REGISTRY = get_registry()


class OperationSummaryOut(BaseModel):
    name: str
    method: str
    tag: str
    summary: str


class OperationDescription(BaseModel):
    name: str
    method: str
    tag: str
    summary: str
    is_command: bool
    request_schema: dict[str, Any] | None
    response_schema: dict[str, Any] | None
    query_parameters: list[dict[str, Any]]


def _get_operation_or_raise(operation: str) -> Operation:
    op = _REGISTRY.get(operation)
    if op is None:
        raise ToolError(
            f"Unknown operation {operation!r}. Use clm_list_operations to see what's available."
        )
    return op


def _validate_params(operation: Operation, params: dict[str, Any]) -> None:
    """Validate `params` before the HTTP call, so a malformed request fails
    fast with a specific message rather than an opaque upstream error."""
    if operation.request_schema is not None:
        try:
            jsonschema.validate(instance=params, schema=operation.request_schema)
        except jsonschema.ValidationError as exc:
            raise ToolError(f"{operation.name}: invalid params — {exc.message}") from exc
        return

    if operation.parameters:
        allowed = {p["name"] for p in operation.parameters}
        unexpected = sorted(set(params) - allowed)
        if unexpected:
            raise ToolError(
                f"{operation.name}: unexpected parameter(s) {unexpected}; "
                f"expected one of {sorted(allowed)}."
            )


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    def clm_list_operations(
        tag: str | None = None, search: str | None = None, limit: int = 50
    ) -> list[OperationSummaryOut]:
        """List CLM API operations reachable via clm_invoke.

        Filter by exact `tag` (e.g. "ShipmentCommand") and/or a
        case-insensitive `search` substring against the operation name or
        summary. Use clm_describe_operation on a result to get its full
        input schema before calling clm_invoke.
        """
        results = _REGISTRY.list_operations(tag=tag, search=search)[: max(limit, 0)]
        return [
            OperationSummaryOut(name=o.name, method=o.method, tag=o.tag, summary=o.summary)
            for o in results
        ]

    @mcp.tool(annotations=_READ_ONLY)
    def clm_describe_operation(
        operation: str,
    ) -> OperationDescription:
        """Describe one operation: its resolved JSON Schema request body
        (for a POST operation) or query parameters (for a GET operation),
        and its response shape — everything needed to build valid
        `clm_invoke` params.
        """
        op = _get_operation_or_raise(operation)
        return OperationDescription(
            name=op.name,
            method=op.method,
            tag=op.tag,
            summary=op.summary,
            is_command=op.is_command,
            request_schema=op.request_schema,
            response_schema=op.response_schema,
            query_parameters=list(op.parameters),
        )

    @mcp.tool(annotations=_GATEWAY_INVOKE)
    async def clm_invoke(
        ctx: Context[AppContext], operation: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Execute any operation listed by clm_list_operations.

        `params` is validated against the operation's resolved schema
        before the HTTP call. A `*Command` operation (a write) is refused
        if the server was started with CLM_ENABLE_WRITES=false.
        """
        app = ctx.request_context.lifespan_context
        op = _get_operation_or_raise(operation)

        if op.is_command and not app.settings.enable_writes:
            raise ToolError(
                f"{operation} is a write operation (tag {op.tag!r}). This server was started "
                "with CLM_ENABLE_WRITES=false (read-only mode) — unset it or set it to true to "
                "enable writes."
            )

        resolved_params = params or {}
        _validate_params(op, resolved_params)

        result = await app.api_client.call(op, resolved_params)
        return strip_nulls(result)
