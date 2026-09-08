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

from clm_mcp.services_catalog import DEFAULT_SERVICE, SERVICES

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
    # NOTE: every vendored OpenAPI spec's `servers[0].url` is wrong (plain
    # HTTP, missing the gateway prefix) — `api_root_url` is the
    # verified-correct root; see `base_url_for` for how a service's full
    # base URL is derived from it.
    api_root_url: str = Field(default="https://msblocks.selisestage.com/api")
    # DEPRECATED: pre-multi-service shipment-only base URL override. Kept
    # (rather than removed) so every existing `.env`, client config, and
    # test that sets CLM_API_BASE_URL keeps pointing at the same place —
    # equivalent to `CLM_API_BASE_URL_OVERRIDES='{"shipment": "..."}'`.
    api_base_url: str | None = Field(default=None)
    # Per-service overrides, e.g. CLM_API_BASE_URL_OVERRIDES='{"konshub": "https://..."}'.
    api_base_url_overrides: dict[str, str] = Field(default_factory=dict)
    api_connect_timeout_seconds: float = Field(default=10.0)
    api_read_timeout_seconds: float = Field(default=60.0)

    # --- Behavior ---
    # Default-on: every *Command operation may execute (through clm_invoke
    # or a generated write tool) with zero setup. Set CLM_ENABLE_WRITES=false
    # to opt OUT into a read-only server instead (e.g. for a connection you
    # don't want able to mutate live data at all). This is independent of
    # `write_tools` below: it gates *execution*, not tool registration.
    enable_writes: bool = Field(default=True)
    # Which services get one generated write tool per *Command operation
    # (tools/commands.py) — empty by default. With all four services loaded,
    # generating every write tool costs ~78K tokens of tool-definition
    # context on every request (see PLAN.md "The tool-surface explosion"),
    # so `clm_invoke` — always available, already schema-validated and
    # write-gated by `enable_writes` above — is the default write path
    # instead. Comma-separated service slugs, or the literal "all".
    write_tools: str = Field(default="")
    token_refresh_skew_seconds: float = Field(default=90.0)
    max_response_bytes: int = Field(default=50_000)

    # --- Logging ---
    log_level: str = Field(default="INFO")

    def base_url_for(self, slug: str) -> str:
        """The API gateway base URL for one CLM service.

        Precedence: an explicit `api_base_url_overrides` entry for `slug`,
        then (for the shipment service only) the deprecated `api_base_url`
        setting, then the default `{api_root_url}/{gateway_segment}` derived
        from the service catalog.
        """
        override = self.api_base_url_overrides.get(slug)
        if override:
            return override
        if slug == DEFAULT_SERVICE and self.api_base_url:
            return self.api_base_url
        service = SERVICES.get(slug)
        if service is None:
            raise ValueError(
                f"Unknown CLM service {slug!r}; valid services are {sorted(SERVICES)}."
            )
        return f"{self.api_root_url}/{service.gateway_segment}"

    def write_tool_services(self) -> frozenset[str]:
        """Parse `write_tools` into the set of service slugs that should get
        generated per-operation write tools. Empty string -> no services
        (the gateway-first default). "all" -> every service. Raises
        `ValueError` naming the valid slugs for an unknown one — validated
        eagerly at startup (see `__main__.py`), not silently at
        tool-registration time.
        """
        raw = self.write_tools.strip()
        if not raw:
            return frozenset()
        if raw.lower() == "all":
            return frozenset(SERVICES)
        slugs = frozenset(s.strip() for s in raw.split(",") if s.strip())
        unknown = slugs - frozenset(SERVICES)
        if unknown:
            raise ValueError(
                f"CLM_WRITE_TOOLS names unknown service(s) {sorted(unknown)}; valid values "
                f"are 'all' or a comma-separated list from {sorted(SERVICES)}."
            )
        return slugs


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
