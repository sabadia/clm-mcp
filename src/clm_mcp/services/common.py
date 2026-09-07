"""Shared helpers for the domain service modules.

Every curated tool follows the same shape: look up its operation in the
registry, call it, shape the result. Centralizing that here keeps each
service function a short, readable composition instead of repeating
registry-lookup and shaping boilerplate — and keeps tool functions
themselves (see `tools/`) doing nothing but Context plumbing and parameter
mapping, per the "no business logic in tool functions" rule.

`resolve_site_id` implements the one piece of cross-cutting business logic
every site-scoped tool shares: default `SiteId` to the authenticated
caller's own site (from the JWT `site_id` claim) when not given explicitly.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.auth.errors import ClmAuthError
from clm_mcp.enums import resolve_enum_value
from clm_mcp.server import AppContext
from clm_mcp.spec.registry import Operation, get_registry
from clm_mcp.spec.shaping import ShapedListResponse, shape_list_response, strip_nulls

_REGISTRY = get_registry()


def get_operation(name: str) -> Operation:
    """Look up a registered operation by name, raising if it's missing.

    A miss here means the vendored spec drifted from what a service module
    expects (see `scripts/refresh_spec.py`) — a bug to fix, not a business
    outcome to report, hence a plain exception rather than `ToolError`.
    """
    op = _REGISTRY.get(name)
    if op is None:
        raise LookupError(f"Operation {name!r} not found in the registry (spec drift?).")
    return op


async def resolve_site_id(app: AppContext, site_id: str | None) -> str:
    """Return `site_id` if given, else the authenticated caller's own
    `site_id` (from the JWT claims, populated by fetching a token)."""
    if site_id:
        return site_id
    try:
        await app.token_manager.get_access_token()
    except ClmAuthError as exc:
        # See api/client.py's identical catch: TokenManager raises a plain
        # ClmAuthError (missing/invalid credentials, identity service down),
        # and this call site reaches it directly, bypassing api_client — so
        # it needs its own translation to ToolError too, or the MCP SDK
        # reduces it to a useless "Error executing tool X".
        raise ToolError(str(exc)) from exc
    claims = app.token_manager.claims
    if claims is None or not claims.site_id:
        raise ToolError(
            "No site_id was given and none is available from the authenticated "
            "session — pass site_id explicitly."
        )
    return claims.site_id


def resolve_enum_param[E: IntEnum](enum_cls: type[E], value: E | str | int) -> int:
    """`enums.resolve_enum_value`, but safe to call from a tool's request
    path: a bad name raises `ToolError` (reported to the model with its
    helpful "valid names are..." message) instead of `ValueError`.

    A tool function is invoked by the MCP SDK's own dispatch wrapper, which
    treats any exception other than `ToolError`/`MCPError` as an internal
    crash — it discards the exception's message entirely and reports a bare
    "Error executing tool <name>" instead. `enums.resolve_enum_value` raises
    plain `ValueError` (the right general-purpose choice — it has callers
    outside the tool-serving path, e.g. tests), so every *tool-facing* call
    site must go through this wrapper rather than calling it directly.
    """
    try:
        return resolve_enum_value(enum_cls, value)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _compact(params: dict[str, Any]) -> dict[str, Any]:
    """Drop `None`-valued keys from an outgoing request body.

    Verified live against the staging API: `ShipmentQuery/GetShipmentsForListView`
    returns a 500 (`Value cannot be null. (Parameter 'key')`) when
    `OrderByField` is sent as an explicit JSON `null`, but succeeds when the
    key is omitted entirely — the .NET side evidently assigns the DTO
    property a non-null default that an explicit `null` in the JSON payload
    overwrites, while an *absent* key leaves that default untouched. Since
    `call_list`/`call_object` back only the curated *read-only* tools (never
    `clm_invoke`, which must preserve exactly what the caller sent, or the
    generated write tools, which already exclude `None` themselves via
    `model_dump(exclude_none=True)`), applying this once here is safe for
    every curated tool without each one needing to remember it individually.
    """
    return {key: value for key, value in params.items() if value is not None}


async def call_list(
    app: AppContext,
    operation_name: str,
    params: dict[str, Any],
    *,
    fields: list[str] | None = None,
    max_bytes: int | None = None,
) -> ShapedListResponse:
    """Call a list-returning operation and shape its result."""
    op = get_operation(operation_name)
    data, total_count = await app.api_client.call_list(op, _compact(params))
    return shape_list_response(
        data if isinstance(data, list) else [],
        total_count=total_count,
        fields=fields,
        max_bytes=max_bytes if max_bytes is not None else app.settings.max_response_bytes,
    )


async def call_object(
    app: AppContext, operation_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Call a single-object-returning operation and null-strip its result.

    Returns `dict[str, Any]` (not `Any`) deliberately: a concrete dict type
    lets the MCP SDK generate a real JSON Schema for the tool's structured
    output, whereas `Any` produces no output schema at all.
    """
    op = get_operation(operation_name)
    data = await app.api_client.call(op, _compact(params))
    if data is None:
        # Observed live: some Get-by-id operations (e.g. GetShipmentById)
        # report IsSuccess=True with Data=null for an id that doesn't
        # exist, rather than a business-failure envelope — there is no
        # error message to surface, only the absence of a result.
        raise ToolError(f"{operation_name}: no result found for the given parameters.")
    stripped = strip_nulls(data)
    if not isinstance(stripped, dict):
        raise ToolError(
            f"{operation_name}: expected an object response but got {type(stripped).__name__}."
        )
    return stripped
