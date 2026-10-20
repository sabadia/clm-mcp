"""Tests for the `clm-mcp` CLI (login subcommand and help output).

`clm-mcp` itself (running the server) is exercised live via
`build_server`/`register_all` in the other test modules — here we cover
what's specific to the CLI layer: argument parsing, the login flow's
identity-service interaction, and that credentials actually land on disk
with the right permissions.
"""

from __future__ import annotations

import json
import os
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


def test_login_uses_env_credentials_without_prompting(monkeypatch: pytest.MonkeyPatch) -> None:
    """With CLM_USER(NAME) + CLM_PASSWORD already in the environment, `login`
    must use them directly rather than prompting for a username."""
    monkeypatch.setenv("CLM_USER", "env@example.com")
    monkeypatch.setenv("CLM_PASSWORD", "env-secret")
    get_settings.cache_clear()

    def _no_prompt(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("login prompted despite env credentials")

    monkeypatch.setattr("builtins.input", _no_prompt)
    monkeypatch.setattr(cli_module.getpass, "getpass", _no_prompt)
    settings = get_settings()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(IDENTITY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "unused",
                    "refresh_token": "env-rt",
                    "expires_in": 420,
                    "token_type": "bearer",
                },
            )
        )
        result = runner.invoke(cli_module.app, ["login"])

    assert result.exit_code == 0, result.output
    body = route.calls.last.request.content.decode()
    assert "username=env%40example.com" in body
    assert "password=env-secret" in body
    saved = json.loads(settings.credentials_path.read_text())
    assert saved["refresh_token"] == "env-rt"
    assert saved["username"] == "env@example.com"


PROD_IDENTITY_URL = "https://prod.test/api/identity/v25/identity/token"


def _write_prod_profile() -> None:
    Path(os.environ["CLM_PROFILES_FILE"]).write_text(
        f"""
[profiles.prod]
identity_token_url = "{PROD_IDENTITY_URL}"
identity_origin = "https://prod.test/"
username_env = "CLM_PROD_USER"
password_env = "CLM_PROD_PASSWORD"
"""
    )


@pytest.mark.parametrize("argv", [["login", "--profile", "prod"], ["--profile", "prod", "login"]])
def test_login_with_profile_uses_its_identity_service_and_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> None:
    _write_prod_profile()
    monkeypatch.setenv("CLM_PROD_USER", "prod@example.com")
    monkeypatch.setenv("CLM_PROD_PASSWORD", "prod-secret")
    # Global credentials for a *different* environment must not be used.
    monkeypatch.setenv("CLM_USER", "stage@example.com")
    monkeypatch.setenv("CLM_PASSWORD", "stage-secret")
    get_settings.cache_clear()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(PROD_IDENTITY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "unused",
                    "refresh_token": "prod-rt",
                    "expires_in": 420,
                    "token_type": "bearer",
                },
            )
        )
        result = runner.invoke(cli_module.app, argv)

    assert result.exit_code == 0, result.output
    assert "profile 'prod'" in result.output
    body = route.calls.last.request.content.decode()
    assert "username=prod%40example.com" in body
    assert route.calls.last.request.headers["Origin"] == "https://prod.test/"
    saved_to = tmp_path / "credentials-prod.json"
    assert f"Credentials saved to {saved_to}" in result.output
    assert json.loads(saved_to.read_text())["refresh_token"] == "prod-rt"


def test_unknown_profile_fails_cleanly_before_starting_the_server() -> None:
    _write_prod_profile()
    result = runner.invoke(cli_module.app, ["--profile", "nope"])
    assert result.exit_code == 1
    assert "'nope'" in result.output
    assert "['prod']" in result.output


def test_unknown_profile_fails_cleanly_for_login() -> None:
    result = runner.invoke(cli_module.app, ["login", "--profile", "nope"])
    assert result.exit_code == 1
    assert "profiles file" in result.output
