"""Data models for the SELISE identity token flow and its JWT claims.

Shapes were confirmed against the live staging identity service
(`POST /api/identity/v25/identity/token`) during design — see PLAN.md
"Verified facts" for the experiment notes.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class TokenResponse(BaseModel):
    """Successful response body from the identity token endpoint.

    Both the `password` and `refresh_token` grants return this same shape.
    Note `expires_in` is seconds (420 == 7 minutes on this tenant) — short
    enough that proactive refresh is mandatory, not an optimization.
    """

    model_config = ConfigDict(populate_by_name=True)

    access_token: SecretStr
    refresh_token: SecretStr
    token_type: str
    expires_in: int
    scope: str | None = None
    ip_address: str | None = None
    may_access: str | None = None


class TokenErrorResponse(BaseModel):
    """Error body from the identity token endpoint (HTTP 400).

    Observed `error` values: `incorrect_user_name_or_password`,
    `invalid_grant` (bad/expired refresh token),
    `http_origin_or_referer_header_is_require` (missing Origin header).
    """

    error: str
    error_description: str | None = None


class JwtClaims(BaseModel):
    """The subset of access-token JWT claims this server relies on.

    IMPORTANT: this is parsed from the JWT payload WITHOUT verifying the
    RS256 signature. That is intentional and safe here — the token itself
    is never used for an authorization decision by this server, only as a
    bearer credential forwarded to the CLM API (which does its own
    verification) and as a source of display/default values (e.g. the
    caller's `site_id`, for defaulting a tool's `SiteId` parameter). Never
    use unverified claims from this model to make a security decision.
    """

    model_config = ConfigDict(extra="ignore")

    sub: str
    user_id: str
    tenant_id: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    display_name: str | None = None
    user_name: str | None = None
    email: str | None = None
    role: list[str] = Field(default_factory=list)
    iat: int
    exp: int

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, tz=UTC)

    @property
    def seconds_until_expiry(self) -> float:
        return self.exp - time.time()


class StoredCredentials(BaseModel):
    """Shape persisted to disk by `clm-mcp login` at
    `~/.config/clm-mcp/credentials.json` (mode 0600).

    Only a refresh token is ever persisted — never a password.
    """

    refresh_token: SecretStr
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    username: str | None = None
