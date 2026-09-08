"""HTTP client for the CLM business API.

Owns request construction (Bearer auth injection, JSON body/query params),
the retry policy, and delegates business-envelope checking to
`api/envelope.py`. Every public method fails, if at all, with `ToolError` —
tool handlers need no exception handling of their own.

Retry policy (deliberately asymmetric — see PLAN.md):
  - `401` forces one proactive-token-manager refresh and one retry,
    regardless of operation type. A 401 means the request never reached
    business logic, so retrying it is always safe.
  - `429` / `5xx` get exponential backoff with jitter, up to 3 attempts,
    but **only for `*Query` operations**. `*Command` operations are never
    safe to blindly retry on a server error — the mutation may have already
    applied — so a `*Command` gets exactly one attempt for these statuses.
  - A network-level timeout follows the same query-only retry rule.

One `ClmApiClient` is constructed in the server lifespan (see `server.py`)
around a single shared `httpx.AsyncClient` (whose connect/read timeouts are
configured there) and a single `TokenManager`, and is used by every tool
call for the life of the process.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.api.envelope import extract_total_count, unwrap_envelope
from clm_mcp.api.errors import ClmApiError, ClmHttpError, UnexpectedResponseShapeError
from clm_mcp.auth.errors import ClmAuthError
from clm_mcp.auth.token_manager import TokenManager
from clm_mcp.config import Settings
from clm_mcp.logging import get_logger
from clm_mcp.spec.registry import Operation

logger = get_logger(__name__)

MAX_QUERY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 8.0


def _is_retryable_status(status_code: int) -> bool:
    return status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= 500


class ClmApiClient:
    """Executes `Operation`s from the `OperationRegistry` against the live API."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        token_manager: TokenManager,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._token_manager = token_manager
        self._sleep = sleep

    async def call(self, operation: Operation, params: dict[str, Any] | None = None) -> Any:
        """Execute `operation` and return its unwrapped business payload.

        `params` is the request body for a POST operation, or the query
        parameters for a GET operation. Raises `ToolError` on any failure —
        transport (unreachable, timed out, non-2xx after retries) or
        business (the API's own envelope reporting failure inside an
        HTTP-200 response; see `api/envelope.py`).
        """
        body = await self._fetch_body(operation, params)
        return unwrap_envelope(body, operation_name=operation.name)

    async def call_list(
        self, operation: Operation, params: dict[str, Any] | None = None
    ) -> tuple[Any, int | None]:
        """Like `call()`, but also returns the envelope's `TotalCount`.

        For tools backing `spec.shaping.shape_list_response`, which reports
        how many rows exist beyond what was returned — information `call()`
        alone discards along with the rest of the envelope.
        """
        body = await self._fetch_body(operation, params)
        data = unwrap_envelope(body, operation_name=operation.name)
        return data, extract_total_count(body)

    async def _fetch_body(self, operation: Operation, params: dict[str, Any] | None) -> Any:
        try:
            response = await self._request_with_retry(operation, params)
        except ClmAuthError as exc:
            # Raised by TokenManager (missing/invalid credentials, identity
            # service down) — always a business-relevant outcome the model
            # should see verbatim (e.g. "run `clm-mcp login`"), never an
            # internal crash the MCP SDK would otherwise reduce to a bare
            # "Error executing tool X".
            raise ToolError(str(exc)) from exc
        except ClmApiError as exc:
            raise ToolError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"{operation.name}: failed to reach the CLM API ({exc}).") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise ToolError(
                str(UnexpectedResponseShapeError(operation_name=operation.name, detail=str(exc)))
            ) from exc

    async def _request_with_retry(
        self, operation: Operation, params: dict[str, Any] | None
    ) -> httpx.Response:
        allow_transport_retry = operation.is_query
        auth_retry_used = False
        attempt = 0

        while True:
            attempt += 1
            access_token = await self._token_manager.get_access_token()

            try:
                response = await self._send_once(operation, params, access_token)
            except httpx.TimeoutException as exc:
                if allow_transport_retry and attempt < MAX_QUERY_ATTEMPTS:
                    logger.warning(
                        "api_client.timeout_retry", operation=operation.name, attempt=attempt
                    )
                    await self._backoff(attempt)
                    continue
                raise ClmApiError(
                    f"{operation.name}: timed out contacting the CLM API ({exc})."
                ) from exc

            if response.status_code == httpx.codes.UNAUTHORIZED and not auth_retry_used:
                auth_retry_used = True
                logger.info("api_client.unauthorized_forcing_refresh", operation=operation.name)
                await self._token_manager.force_refresh()
                continue

            if (
                _is_retryable_status(response.status_code)
                and allow_transport_retry
                and attempt < MAX_QUERY_ATTEMPTS
            ):
                logger.warning(
                    "api_client.retryable_status",
                    operation=operation.name,
                    status_code=response.status_code,
                    attempt=attempt,
                )
                await self._backoff(attempt)
                continue

            if response.status_code >= httpx.codes.BAD_REQUEST:
                raise ClmHttpError(
                    operation_name=operation.name,
                    status_code=response.status_code,
                    body=response.text,
                )

            return response

    async def _send_once(
        self, operation: Operation, params: dict[str, Any] | None, access_token: str
    ) -> httpx.Response:
        url = f"{self._settings.base_url_for(operation.service)}{operation.path}"
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        if operation.method == "get":
            return await self._http.get(url, headers=headers, params=params or {})

        headers["Content-Type"] = "application/json"
        return await self._http.post(url, headers=headers, json=params or {})

    async def _backoff(self, attempt: int) -> None:
        base = min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_CAP_SECONDS)
        jitter = random.uniform(0, base * 0.25)  # noqa: S311 - jitter, not security-sensitive
        await self._sleep(base + jitter)
