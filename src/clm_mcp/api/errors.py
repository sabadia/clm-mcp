"""Transport/HTTP-level exceptions raised by `api.client.ClmApiClient`.

Business-level failures (the API's own success/failure envelope, present in
the response *body* regardless of HTTP status — see `api/envelope.py`) are
handled separately and raise `ToolError` directly, since a business failure
is exactly the kind of outcome a tool call should report back to the model
as a normal (if unsuccessful) result, not an internal error.

The exceptions here are for genuine transport/protocol problems: a non-2xx
HTTP response the retry policy did not recover from, or a response body
that isn't parseable JSON. `api/client.py` catches these too and re-raises
as `ToolError`, so every public `ClmApiClient` method fails, if at all, with
a `ToolError` a tool handler can let propagate unmodified.
"""

from __future__ import annotations


class ClmApiError(Exception):
    """Base class for transport-level CLM business API failures."""


class ClmHttpError(ClmApiError):
    """A non-2xx HTTP response the retry policy did not recover from."""

    def __init__(self, *, operation_name: str, status_code: int, body: str) -> None:
        self.operation_name = operation_name
        self.status_code = status_code
        self.body = body
        super().__init__(f"{operation_name} failed with HTTP {status_code}: {body[:500]}")


class UnexpectedResponseShapeError(ClmApiError):
    """The response body was not valid JSON."""

    def __init__(self, *, operation_name: str, detail: str) -> None:
        self.operation_name = operation_name
        super().__init__(f"{operation_name} returned an unparseable response: {detail}")
