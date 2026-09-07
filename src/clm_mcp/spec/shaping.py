"""Shrinks CLM API responses down to what an LLM tool call actually needs.

The API's DTOs are large and mostly-null (`Shipment` alone has 142
properties; `CockpitListViewShipmentDto` has 41 — see PLAN.md). Handing one
of these to the model unshaped either wastes a large fraction of the
response on null fields or, for a list endpoint, risks flooding the
context entirely. Three independent techniques address this, in order:

  1. `strip_nulls` — drop every dict key whose value is `None`. Safe by
     construction: a JSON object has no positional meaning, so removing an
     absent-ish field changes nothing a consumer could rely on. List
     *elements* are never dropped this way (their position/count can be
     meaningful), only recursed into.
  2. `project_fields` — keep only the caller-requested top-level field
     names on each item, when a curated tool exposes a `fields` parameter.
  3. `shape_list_response` — combine both, then enforce a hard byte budget
     on the serialized result, trimming the row count (never truncating a
     row's own fields) and returning a structured, self-describing result
     so the model knows a limit was hit rather than assuming it saw
     everything.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

DEFAULT_MAX_RESPONSE_BYTES = 50_000


class ShapedListResponse(BaseModel):
    """Structured output for every curated list-returning tool.

    `total_count` is the API's own count of matching rows (from the
    envelope's `TotalCount`, when the operation reports one) and may exceed
    `returned` even when `truncated` is False, e.g. because the caller's
    own page size already limited the API-side result.
    """

    data: list[Any]
    total_count: int | None = None
    returned: int
    truncated: bool
    note: str | None = None


def strip_nulls(value: Any) -> Any:
    """Recursively drop `None`-valued keys from dicts. List length and
    element order are always preserved — only dict keys are ever removed."""
    if isinstance(value, dict):
        return {key: strip_nulls(v) for key, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nulls(item) for item in value]
    return value


def project_fields(data: Any, fields: list[str] | None) -> Any:
    """Keep only `fields` (top-level keys) on each dict in `data`.

    `data` may be a single dict or a list of dicts (or anything else,
    returned unchanged). A falsy `fields` is a no-op, so tools can pass
    their `fields` parameter straight through without an `if` at the call
    site.
    """
    if not fields:
        return data

    wanted = set(fields)

    def _pick(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: value for key, value in item.items() if key in wanted}
        return item

    if isinstance(data, list):
        return [_pick(item) for item in data]
    return _pick(data)


def shape_list_response(
    data: list[Any] | None,
    *,
    total_count: int | None = None,
    fields: list[str] | None = None,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> ShapedListResponse:
    """Null-strip, project, and byte-cap a list response into the
    structured shape every curated list tool returns.

    On overflow, rows are dropped from the end (never partially — a row is
    either whole or absent) until the serialized result fits `max_bytes`,
    and `note` explains what happened and how to get the rest (narrow
    filters/pagination, or pass `fields` to shrink each row).
    """
    original_count = len(data) if data else 0
    shaped = project_fields(strip_nulls(data or []), fields)

    if _byte_size(shaped) <= max_bytes:
        return ShapedListResponse(
            data=shaped, total_count=total_count, returned=len(shaped), truncated=False
        )

    kept = _fit_to_byte_budget(shaped, max_bytes)
    if kept:
        note = (
            f"Response truncated to {len(kept)} of {original_count} rows to stay within the "
            f"{max_bytes}-byte limit. Narrow your filters or page size to see the rest, or "
            "pass `fields` to shrink each row and fit more of them."
        )
    else:
        note = (
            f"Even a single row exceeds the {max_bytes}-byte limit, so none could be returned. "
            "Pass `fields` to request only the columns you need."
        )
    return ShapedListResponse(
        data=kept, total_count=total_count, returned=len(kept), truncated=True, note=note
    )


def _byte_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _fit_to_byte_budget(items: list[Any], max_bytes: int) -> list[Any]:
    """Binary-search the largest row-count prefix of `items` whose
    serialized size fits `max_bytes`."""
    low, high = 0, len(items)
    while low < high:
        mid = (low + high + 1) // 2
        if _byte_size(items[:mid]) <= max_bytes:
            low = mid
        else:
            high = mid - 1
    return items[:low]
