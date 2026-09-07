"""Structured logging configuration.

Under the stdio transport, stdout **is** the JSON-RPC channel — any stray
byte written there (a stray ``print()``, a misconfigured logging handler)
corrupts the protocol. All logging in this project must go to stderr, which
is what this module configures. `ruff`'s `T20` rule additionally forbids
`print()` calls anywhere in the source tree.

A redaction processor strips credential-shaped values before they ever reach
a log sink, regardless of transport.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "password",
        "authorization",
        "id_token",
        "client_secret",
    }
)


def _redact_sensitive(
    _logger: structlog.typing.WrappedLogger,
    _method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Recursively redact sensitive-looking keys from a structlog event dict."""

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else scrub(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    result: structlog.typing.EventDict = scrub(event_dict)
    return result


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit exclusively to stderr."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given name (module `__name__`)."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
