"""Proactive, single-flight OAuth2-style token lifecycle management.

The CLM identity service issues access tokens with a **7-minute** lifetime
(`expires_in=420` on the observed tenant), so treating refresh as a lazy,
on-401 concern is not viable for anything but the shortest-lived tool call.
This module instead tracks expiry and refreshes proactively, ahead of a
configurable skew window, and coalesces concurrent callers behind a single
in-flight refresh.

Credential resolution order (first available wins):
  1. an explicit refresh token (`Settings.refresh_token` / `CLM_REFRESH_TOKEN`)
  2. an explicit username + password (`Settings.username`/`password`)
  3. a refresh token loaded from the on-disk store (`clm-mcp login`)
If none is available, `MissingCredentialsError` is raised.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

import httpx
import jwt
from pydantic import ValidationError

from clm_mcp.auth.errors import (
    IdentityServiceError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    MissingCredentialsError,
)
from clm_mcp.auth.models import JwtClaims, TokenErrorResponse, TokenResponse
from clm_mcp.auth.store import load_credentials, save_credentials
from clm_mcp.config import Settings
from clm_mcp.logging import get_logger

logger = get_logger(__name__)

_ERROR_INVALID_GRANT: Final = "invalid_grant"
_ERROR_INCORRECT_CREDENTIALS: Final = "incorrect_user_name_or_password"


class _CredentialSource(Enum):
    """Where the currently-active refresh token came from.

    Used only to decide whether a rotated refresh token should be written
    back to the on-disk store: env-sourced credentials are the user's
    explicit configuration and are left alone, but a token that came from
    the store is kept in sync so the server keeps working unattended if the
    identity service starts rotating refresh tokens in the future (it does
    not today — see PLAN.md "Verified facts" — but nothing guarantees that).
    """

    REFRESH_TOKEN_SETTING = auto()
    PASSWORD_SETTING = auto()
    STORE = auto()


@dataclass
class _ActiveToken:
    response: TokenResponse
    claims: JwtClaims
    monotonic_expiry: float
    source: _CredentialSource


class TokenManager:
    """Owns the lifecycle of a single CLM identity access/refresh token pair.

    One instance is created per server process (in the lifespan) and shared
    by every tool invocation; `get_access_token()` is safe to call
    concurrently.
    """

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._active: _ActiveToken | None = None
        # The refresh token currently believed valid, independent of a live
        # access token, so a fresh process can refresh immediately.
        self._refresh_token: str | None = None
        self._refresh_token_source: _CredentialSource | None = None

    @property
    def claims(self) -> JwtClaims | None:
        """Claims of the currently cached access token, if any is held.

        Does not trigger a refresh; may be stale or absent. Callers needing
        a guaranteed-fresh token should call `get_access_token()` first.
        """
        return self._active.claims if self._active else None

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed."""
        if self._is_active_token_fresh():
            assert self._active is not None  # noqa: S101 (narrows for mypy after the check)
            return self._active.response.access_token.get_secret_value()

        async with self._lock:
            # Re-check after acquiring the lock: another task may have just
            # completed the refresh we were about to duplicate.
            if self._is_active_token_fresh():
                assert self._active is not None  # noqa: S101
                return self._active.response.access_token.get_secret_value()
            await self._refresh()

        assert self._active is not None  # noqa: S101
        return self._active.response.access_token.get_secret_value()

    async def force_refresh(self) -> str:
        """Unconditionally refresh, bypassing the freshness check.

        Used by the API client when the business API itself returns 401 —
        a signal the access token was rejected despite our own bookkeeping
        believing it valid (clock skew, server-side revocation, etc.).
        """
        async with self._lock:
            await self._refresh()
        assert self._active is not None  # noqa: S101
        return self._active.response.access_token.get_secret_value()

    def _is_active_token_fresh(self) -> bool:
        if self._active is None:
            return False
        remaining = self._active.monotonic_expiry - self._clock()
        return remaining > self._settings.token_refresh_skew_seconds

    async def _refresh(self) -> None:
        """Obtain a new access token, trying refresh-token then password grant."""
        refresh_token = self._resolve_refresh_token()
        if refresh_token is not None:
            token, source = refresh_token
            try:
                response = await self._request_token(
                    grant_type="refresh_token", refresh_token=token
                )
                await self._store_active_token(response, source)
                return
            except InvalidRefreshTokenError:
                logger.warning("token_manager.refresh_token_rejected", source=source.name)
                # Fall through to a password grant, if credentials exist.

        password_credentials = self._resolve_password_credentials()
        if password_credentials is not None:
            username, password = password_credentials
            response = await self._request_token(
                grant_type="password", username=username, password=password
            )
            await self._store_active_token(response, _CredentialSource.PASSWORD_SETTING)
            return

        raise MissingCredentialsError()

    def _resolve_refresh_token(self) -> tuple[str, _CredentialSource] | None:
        if self._refresh_token is not None and self._refresh_token_source is not None:
            return self._refresh_token, self._refresh_token_source

        if self._settings.refresh_token is not None:
            return (
                self._settings.refresh_token.get_secret_value(),
                _CredentialSource.REFRESH_TOKEN_SETTING,
            )

        stored = load_credentials(self._settings.credentials_path)
        if stored is not None:
            return stored.refresh_token.get_secret_value(), _CredentialSource.STORE

        return None

    def _resolve_password_credentials(self) -> tuple[str, str] | None:
        if self._settings.username and self._settings.password:
            return self._settings.username, self._settings.password.get_secret_value()
        return None

    async def _store_active_token(self, response: TokenResponse, source: _CredentialSource) -> None:
        try:
            claims = _decode_claims(response.access_token.get_secret_value())
        except (jwt.PyJWTError, ValidationError) as exc:
            # A malformed/undecodable token, or one missing claims JwtClaims
            # requires (sub/user_id/iat/exp) — never observed live, but if
            # the identity service ever did this, the raw PyJWT/pydantic
            # exception would otherwise propagate uncaught: neither is a
            # ClmAuthError, so none of the three call sites that translate
            # ClmAuthError -> ToolError would catch it, and the MCP SDK
            # would reduce it to a bare "Error executing tool X" — the same
            # bug class fixed for missing/invalid credentials.
            raise IdentityServiceError(
                f"The identity service returned an unusable access token: {exc}"
            ) from exc
        monotonic_now = self._clock()

        # Cross-check the identity service's `expires_in` against the JWT's
        # own `exp` claim and trust whichever implies the *earlier* expiry —
        # protects against the two ever disagreeing (clock skew between
        # services, or a future change to either value).
        expiry_from_ttl = monotonic_now + response.expires_in
        expiry_from_claim = monotonic_now + claims.seconds_until_expiry
        monotonic_expiry = min(expiry_from_ttl, expiry_from_claim)

        self._active = _ActiveToken(
            response=response, claims=claims, monotonic_expiry=monotonic_expiry, source=source
        )

        new_refresh_token = response.refresh_token.get_secret_value()
        token_rotated = new_refresh_token != self._refresh_token
        self._refresh_token = new_refresh_token
        self._refresh_token_source = source

        # See _CredentialSource docstring: only the store is a cache we own
        # and keep in sync; env-sourced credentials are left untouched.
        # Offloaded to a thread: save_credentials does synchronous file I/O
        # (open/write/chmod/rename), and this runs while _lock is held —
        # blocking the event loop here would stall every other in-flight
        # tool call for the duration of that write.
        if source == _CredentialSource.STORE and token_rotated:
            await asyncio.to_thread(
                save_credentials,
                self._settings.credentials_path,
                new_refresh_token,
                claims.user_name,
            )

        logger.info(
            "token_manager.refreshed",
            source=source.name,
            expires_in=response.expires_in,
            user=claims.user_name,
            site_id=claims.site_id,
        )

    async def _request_token(self, *, grant_type: str, **fields: str) -> TokenResponse:
        # The Origin header is required by this identity service — omitting
        # it returns 400 http_origin_or_referer_header_is_require (verified
        # live during design; see PLAN.md).
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self._settings.identity_origin,
        }
        data = {"grant_type": grant_type, **fields}

        try:
            http_response = await self._http.post(
                self._settings.identity_token_url, headers=headers, data=data
            )
        except httpx.HTTPError as exc:
            raise IdentityServiceError(f"Identity service unreachable: {exc}") from exc

        if http_response.status_code == httpx.codes.OK:
            return TokenResponse.model_validate_json(http_response.content)

        error = _parse_token_error(http_response)
        if error == _ERROR_INCORRECT_CREDENTIALS:
            raise InvalidCredentialsError("Incorrect username or password.")
        if error == _ERROR_INVALID_GRANT:
            raise InvalidRefreshTokenError("The refresh token was rejected (expired or revoked).")
        raise IdentityServiceError(
            f"Identity service returned {http_response.status_code}: {error or http_response.text}"
        )


def _decode_claims(access_token: str) -> JwtClaims:
    """Decode JWT claims WITHOUT verifying the RS256 signature.

    See `JwtClaims` docstring for why this is safe here: the token is a
    bearer credential verified by the CLM API itself, never used by this
    server to make an authorization decision.
    """
    payload = jwt.decode(access_token, options={"verify_signature": False})
    return JwtClaims.model_validate(payload)


def _parse_token_error(response: httpx.Response) -> str | None:
    # ValueError covers both pydantic's ValidationError (a ValueError subclass) and a
    # plain JSON decode failure — either way, the body just isn't the expected shape;
    # the caller falls back to IdentityServiceError with the raw response text.
    try:
        return TokenErrorResponse.model_validate_json(response.content).error
    except ValueError:
        return None
