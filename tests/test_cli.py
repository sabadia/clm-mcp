"""Tests for the `clm-mcp` CLI (login subcommand and help output).

`clm-mcp` itself (running the server) is exercised live via
`build_server`/`register_all` in the other test modules — here we cover
what's specific to the CLI layer: argument parsing, the login flow's
identity-service interaction, and that credentials actually land on disk
with the right permissions.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from clm_mcp import __main__ as cli_module
from clm_mcp.config import get_settings

IDENTITY_URL = "https://clm.selisestage.com/api/identity/v25/identity/token"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None]:
    """Point CLM_CREDENTIALS_PATH at a temp dir and clear the cached
    Settings singleton so each test gets an isolated credentials file.

    Also stubs out `configure_logging`: `typer.testing.CliRunner` redirects
    and then closes stderr around each `invoke()`, and `configure_logging`
    points structlog's *global* logger factory at whatever stderr object is
    current when called — pointing it at that since-closed stream would
    break every other test's logging for the rest of the pytest session.
    """
    monkeypatch.setenv("CLM_IDENTITY_TOKEN_URL", IDENTITY_URL)
    monkeypatch.setenv("CLM_CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(cli_module, "configure_logging", lambda *_args, **_kwargs: None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_help_shows_login_subcommand() -> None:
    result = runner.invoke(cli_module.app, ["--help"])
    assert result.exit_code == 0
    assert "login" in result.output


def test_login_with_refresh_token_saves_credentials() -> None:
    settings = get_settings()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(IDENTITY_URL).mock(return_value=httpx.Response(200, json={}))
        result = runner.invoke(cli_module.app, ["login", "--refresh-token", "good-token"])

    assert result.exit_code == 0, result.output
    assert "Credentials saved" in result.output
    saved = json.loads(settings.credentials_path.read_text())
    assert saved["refresh_token"] == "good-token"
    assert oct(settings.credentials_path.stat().st_mode)[-3:] == "600"


def test_login_with_bad_refresh_token_fails_cleanly() -> None:
    settings = get_settings()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "The token is invalid."}
            )
        )
        result = runner.invoke(cli_module.app, ["login", "--refresh-token", "bad-token"])

    assert result.exit_code == 1
    assert "invalid" in result.output.lower()
    assert not settings.credentials_path.exists()


def test_run_rejects_unknown_write_tools_service_before_starting_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLM_WRITE_TOOLS naming an unknown service must fail fast with a clear
    CLI message, never reaching server.run() (which would otherwise block
    on stdio and hang this test)."""
    monkeypatch.setenv("CLM_WRITE_TOOLS", "not-a-real-service")
    get_settings.cache_clear()

    result = runner.invoke(cli_module.app, [])

    assert result.exit_code == 1
    assert "unknown service" in result.output


def test_login_identity_service_unreachable() -> None:
    settings = get_settings()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(IDENTITY_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        result = runner.invoke(cli_module.app, ["login", "--refresh-token", "any-token"])

    assert result.exit_code == 1
    assert "Could not reach" in result.output
    assert not settings.credentials_path.exists()
