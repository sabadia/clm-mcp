"""Application settings.

All configuration is read from environment variables (or a `.env` file) with
the ``CLM_`` prefix. See ``.env.example`` at the repo root for the full list
and defaults.

Optionally, named profiles in a TOML file (``profiles.example.toml``) bundle
the settings for one CLM environment (e.g. stage vs. prod) and name the
environment variables its credentials come from; see `load_settings`.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from clm_mcp.services_catalog import DEFAULT_SERVICE, SERVICES

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "clm-mcp"
DEFAULT_CREDENTIALS_PATH = DEFAULT_CONFIG_DIR / "credentials.json"
DEFAULT_PROFILES_PATH = DEFAULT_CONFIG_DIR / "profiles.toml"


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
    # CLM_USER is accepted as an alias for CLM_USERNAME (CLM_USERNAME wins
    # if both are set); "username" keeps `Settings(username=...)` working.
    username: str | None = Field(
        default=None, validation_alias=AliasChoices("CLM_USERNAME", "CLM_USER", "username")
    )
    password: SecretStr | None = Field(default=None)
    credentials_path: Path = Field(default=DEFAULT_CREDENTIALS_PATH)

    # --- Profiles (see `load_settings`) ---
    # The active profile's name, or None when no profile is in use. Set via
    # `--profile`, CLM_PROFILE, or the profiles file's top-level `default`.
    profile: str | None = Field(default=None)
    profiles_file: Path = Field(default=DEFAULT_PROFILES_PATH)

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


class ProfileError(ValueError):
    """The profiles file, or the selected profile within it, is unusable."""


# Credentials a profile must name an env var for (`<field>_env`) rather than
# embed in the file, so the file itself never holds a secret.
_SECRET_FIELDS = frozenset({"password", "refresh_token"})
_CREDENTIAL_FIELDS = frozenset({"username"}) | _SECRET_FIELDS
# Selecting the profile is not something a profile can itself configure.
_NON_PROFILE_FIELDS = frozenset({"profile", "profiles_file"})


def load_settings(profile: str | None = None) -> Settings:
    """Build Settings, applying a named profile from the profiles file if one
    is selected.

    Profile selection (first set wins): the `profile` argument (`--profile`),
    CLM_PROFILE, then the profiles file's top-level `default` key. With no
    profile selected, this is exactly `Settings()`.

    Within an active profile:
      - any Settings field may be set directly (`api_root_url = "..."`), and
        wins over the corresponding CLM_* env var;
      - any field may instead be read from a named env var
        (`password_env = "CLM_PROD_PASSWORD"`) — the only way to supply
        `password`/`refresh_token`, which may not appear literally;
      - credentials (`username`/`password`/`refresh_token`) are never
        inherited from the global CLM_* env vars, since those may belong to
        a different environment;
      - `credentials_path` defaults to `credentials-<profile>.json`, so each
        profile's `clm-mcp login` is stored separately;
      - every other field falls back to its CLM_* env var, then its default.
    """
    base = Settings()
    path = base.profiles_file.expanduser()
    explicit = profile or base.profile

    profiles_doc = _read_profiles_file(path) if path.is_file() else None
    if explicit is None:
        explicit = profiles_doc.get("default") if profiles_doc else None
        if explicit is None:
            return base
    if profiles_doc is None:
        raise ProfileError(
            f"Profile {explicit!r} was requested but the profiles file {path} does not exist."
        )

    profiles = profiles_doc.get("profiles", {})
    if explicit not in profiles:
        raise ProfileError(
            f"Profile {explicit!r} is not defined in {path}; "
            f"available profiles: {sorted(profiles)}."
        )
    overrides = _resolve_profile(explicit, profiles[explicit], path)
    overrides.setdefault(
        "credentials_path", DEFAULT_CREDENTIALS_PATH.with_name(f"credentials-{explicit}.json")
    )
    return Settings(**overrides)


def _read_profiles_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"Could not read the profiles file {path}: {exc}") from exc


def _resolve_profile(name: str, table: Any, path: Path) -> dict[str, Any]:
    """Turn one `[profiles.<name>]` table into Settings keyword arguments."""
    if not isinstance(table, dict):
        raise ProfileError(f"[profiles.{name}] in {path} must be a table.")
    fields = set(Settings.model_fields) - _NON_PROFILE_FIELDS
    where = f"[profiles.{name}] in {path}"

    # Explicit None for every credential, so the global CLM_* credential env
    # vars (lower precedence than these kwargs) can never leak in.
    resolved: dict[str, Any] = dict.fromkeys(_CREDENTIAL_FIELDS)
    for key, value in table.items():
        if key.endswith("_env") and key.removesuffix("_env") in fields:
            field = key.removesuffix("_env")
            if field in table:
                raise ProfileError(f"{where} sets both {field!r} and {key!r}; use one.")
            if value not in os.environ:
                raise ProfileError(
                    f"{where} reads {field!r} from ${value}, but that environment "
                    "variable is not set."
                )
            resolved[field] = os.environ[value]
        elif key in _SECRET_FIELDS:
            raise ProfileError(
                f"{where} contains a literal {key!r}; secrets must not be stored in the "
                f'profiles file — use {key}_env = "SOME_ENV_VAR" instead.'
            )
        elif key in fields:
            resolved[key] = value
        else:
            raise ProfileError(
                f"{where} has unknown key {key!r}. Valid keys are the settings "
                f"{sorted(fields)}, or any of them with an `_env` suffix."
            )
    if isinstance(resolved.get("credentials_path"), str):
        resolved["credentials_path"] = Path(resolved["credentials_path"]).expanduser()
    resolved["profile"] = name
    return resolved


@lru_cache
def get_settings(profile: str | None = None) -> Settings:
    """Return the process-wide cached Settings (see `load_settings`)."""
    return load_settings(profile)
