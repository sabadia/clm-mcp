"""Unwraps the CLM API's Query/Command response envelopes.

The business API returns **HTTP 200 even when the requested operation
failed** — success/failure lives entirely in the JSON body, in one of two
shapes depending on whether the operation is a `*Query` or `*Command`
(verified against the live staging API during design; see PLAN.md):

  Query   {Data, IsSuccess, StatusCode, ErrorMessage, PropertyName,
           ValidationErrors, TotalCount}
  Command {RequestUri, ExternalError, HttpStatusCode, Errors,
           ErrorMessages, StatusCode}

`unwrap_envelope` treats both uniformly by checking whichever
failure-indicating fields are present, and raises `ToolError` directly on
failure: a business failure (e.g. "shipment not found", a validation error)
is a normal tool outcome to report to the model, not an internal server
error, so it belongs in the same exception type the MCP SDK already uses to
report failed tool calls without a stack trace.

Note `StatusCode`/`HttpStatusCode` in the body are NOT used as a success
signal here — they were observed to vary (e.g. `0` on one successful Query
response, `200` on another) and are not a reliable discriminator on this
API; `IsSuccess`, `ErrorMessages`, `ExternalError`, and
`ValidationErrors.IsValid` are.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.mcpserver.exceptions import ToolError

# Operations confirmed live (via raw `curl`, with two different real
# MaterialIds on two different sites, and independently by the user with
# their own working payload) to *always* report `IsSuccess: false` even on
# a genuine, correct lookup — a defect in this specific operation's own
# response, not a business failure. `Data` is the only reliable signal for
# these: null means "not found" (confirmed: a nonexistent MaterialId
# returns `Data: null` in ~0.8s), a populated object means success
# (confirmed: a real MaterialId returns full, correct usage counts in
# ~9s — this operation is also noticeably slower than most). Scoped to the
# exact canonical operation name so this can never silently mask a real
# failure on an unrelated operation.
_ALWAYS_REPORTS_ISSUCCESS_FALSE_OPERATIONS: Final = frozenset(
    {"construction/ConstructionManagementQuery/GetMaterialUsagesById"}
)


def unwrap_envelope(payload: Any, *, operation_name: str) -> Any:
    """Check a decoded JSON response body for business-level success and
    return its useful payload.

    Returns `payload["Data"]` for a Query-shaped envelope, or the whole
    body for a Command-shaped envelope (which carries no separate `Data`
    field — the envelope itself *is* the result). A body that isn't a dict
    (e.g. a bare file/blob response) is returned unchanged, since there is
    no envelope to check.

    Raises `ToolError` if any of the following indicate failure:
      - `IsSuccess` is explicitly `False`
      - `ErrorMessages` is a non-empty list
      - `ExternalError` is a non-empty string
      - `ValidationErrors.IsValid` is `False`

    One narrow exception: `_ALWAYS_REPORTS_ISSUCCESS_FALSE_OPERATIONS` lists
    operations confirmed to always set `IsSuccess: false` even on success —
    for exactly those, a populated `Data` overrides an otherwise-bare
    `IsSuccess: false` (no error message, no failed validation) and is
    treated as success instead.
    """
    if not isinstance(payload, dict):
        return payload

    validation_errors = payload.get("ValidationErrors")
    validation_failed = (
        isinstance(validation_errors, dict) and validation_errors.get("IsValid") is False
    )
    error_messages = payload.get("ErrorMessages") or []
    external_error = payload.get("ExternalError")
    is_success_flag = payload.get("IsSuccess")

    failed = (
        is_success_flag is False
        or bool(error_messages)
        or bool(external_error)
        or validation_failed
    )

    if (
        failed
        and operation_name in _ALWAYS_REPORTS_ISSUCCESS_FALSE_OPERATIONS
        and is_success_flag is False
        and not payload.get("ErrorMessage")
        and not error_messages
        and not external_error
        and not validation_failed
        and payload.get("Data") is not None
    ):
        failed = False

    if failed:
        message = _build_error_message(
            payload,
            validation_errors=validation_errors,
            error_messages=error_messages,
            external_error=external_error,
        )
        property_name = payload.get("PropertyName")
        if property_name:
            message = f"{message} (field: {property_name})"
        raise ToolError(f"{operation_name} failed: {message}")

    return payload["Data"] if "Data" in payload else payload


def extract_total_count(payload: Any) -> int | None:
    """Read a Query envelope's `TotalCount`, for callers that need it
    alongside `Data` (see `ClmApiClient.call_list`) — e.g. to report
    "returned 20 of 143" rather than just "returned 20".

    Returns `None` for a Command envelope (no such field) or any payload
    where `TotalCount` isn't an int.
    """
    if isinstance(payload, dict):
        total_count = payload.get("TotalCount")
        if isinstance(total_count, int):
            return total_count
    return None


def _build_error_message(
    payload: dict[str, Any],
    *,
    validation_errors: Any,
    error_messages: list[Any],
    external_error: Any,
) -> str:
    error_message = payload.get("ErrorMessage")
    if error_message:
        return str(error_message)
    if error_messages:
        return "; ".join(str(m) for m in error_messages)
    if external_error:
        return str(external_error)
    if isinstance(validation_errors, dict) and validation_errors.get("Errors"):
        return "; ".join(_format_validation_error(e) for e in validation_errors["Errors"])
    return "Request failed (the API returned no error message)."


def _format_validation_error(error: Any) -> str:
    """Format one `ValidationErrors.Errors` entry.

    Observed live: these are FluentValidation `ValidationFailure` objects —
    `{PropertyName, ErrorMessage, AttemptedValue, Severity, ErrorCode,
    FormattedMessagePlaceholderValues, ...}` — not plain strings. Extract
    just the human-readable `ErrorMessage` (prefixed with the field name
    when there is one) rather than dumping the whole object's Python repr,
    which is unreadable and leaks internal field names like
    `FormattedMessagePlaceholderValues` into the model-facing message.
    """
    if isinstance(error, dict) and "ErrorMessage" in error:
        message = str(error["ErrorMessage"])
        property_name = error.get("PropertyName")
        return f"{property_name}: {message}" if property_name else message
    return str(error)
