"""Exceptions raised by the authentication layer.

These are internal exceptions raised by `token_manager`/`store`/`client`
code. Tool-facing code (see `mcp.server.mcpserver.exceptions.ToolError`)
should catch these and translate them into a `ToolError` with a message
safe to show the model — never let a raw exception (which may include
partial credential context in `repr()`) escape to the MCP transport.
"""

from __future__ import annotations


class ClmAuthError(Exception):
    """Base class for all authentication-related failures."""


class InvalidCredentialsError(ClmAuthError):
    """The username/password combination was rejected by the identity service."""


class InvalidRefreshTokenError(ClmAuthError):
    """The refresh token was rejected by the identity service (`invalid_grant`)."""


class MissingCredentialsError(ClmAuthError):
    """No usable credential was found in env, settings, or the on-disk store."""

    def __init__(self) -> None:
        super().__init__(
            "No CLM credentials found. Set CLM_REFRESH_TOKEN, or CLM_USERNAME + "
            "CLM_PASSWORD, or run `clm-mcp login` once to store a refresh token."
        )


class IdentityServiceError(ClmAuthError):
    """The identity service returned an unexpected error or was unreachable."""
