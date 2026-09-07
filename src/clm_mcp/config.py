"""Application settings.

All configuration is read from environment variables (or a `.env` file) with
the ``CLM_`` prefix. See ``.env.example`` at the repo root for the full list
and defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "clm-mcp" / "credentials.json"


class Settings(BaseSettings):
    """Runtime configuration for the CLM MCP server.

    Precedence (highest first), per pydantic-settings defaults: process
    environment variables, then a ``.env`` file in the current working
    directory, then the field defaults below.
    """

    model_config = SettingsConfigDict(
        env_prefix="CLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credentials (optional here; auth.token_manager also checks the
    # on-disk credential store written by `clm-mcp login`) ---
    refresh_token: SecretStr | None = Field(default=None)
    username: str | None = Field(default=None)
    password: SecretStr | None = Field(default=None)
    credentials_path: Path = Field(default=DEFAULT_CREDENTIALS_PATH)

    # --- Identity service ---
    identity_token_url: str = Field(
        default="https://clm.selisestage.com/api/identity/v25/identity/token"
    )
    identity_origin: str = Field(default="https://clm.selisestage.com/")

    # --- Business API ---
    # NOTE: the vendored OpenAPI spec's `servers[0].url` is wrong (plain HTTP,
    # missing the gateway prefix) — this default is the verified-correct base.
    api_base_url: str = Field(default="https://msblocks.selisestage.com/api/business-clm-shipment")
    api_connect_timeout_seconds: float = Field(default=10.0)
    api_read_timeout_seconds: float = Field(default=60.0)

    # --- Behavior ---
    # Default-on: every *Command operation gets its own write tool
    # (tools/commands.py) out of the box, so "every endpoint has a tool" is
    # true with zero setup. Set CLM_ENABLE_WRITES=false to opt OUT into a
    # read-only server instead (e.g. for a connection you don't want able to
    # mutate live data at all).
    enable_writes: bool = Field(default=True)
    token_refresh_skew_seconds: float = Field(default=90.0)
    max_response_bytes: int = Field(default=50_000)

    # --- Logging ---
    log_level: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
