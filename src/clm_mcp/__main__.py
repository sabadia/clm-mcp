"""CLI entry point for clm-mcp.

  clm-mcp                      run the MCP server over stdio (for MCP clients)
  clm-mcp --http --port 8000   run over streamable HTTP instead
  clm-mcp login                interactively store a refresh token
  clm-mcp login --refresh-token <token>   store a given refresh token directly

`clm-mcp login` is the only place a password is ever collected — via
`getpass` on the terminal, never as a tool argument — per the MCP
specification's "servers MUST NOT use elicitation to request sensitive
information." The resulting refresh token is written to
`~/.config/clm-mcp/credentials.json` (mode 0600) and picked up automatically
by future `clm-mcp` runs; nothing needs to be passed on the command line
after that.
"""

from __future__ import annotations

import asyncio
import getpass
from typing import Annotated

import httpx
import typer

from clm_mcp.auth.models import TokenErrorResponse, TokenResponse
from clm_mcp.auth.store import save_credentials
from clm_mcp.config import Settings, get_settings
from clm_mcp.logging import configure_logging, get_logger
from clm_mcp.server import build_server
from clm_mcp.tools import register_all

logger = get_logger(__name__)

app = typer.Typer(
    name="clm-mcp",
    help="MCP server for the SELISE CLM APIs (shipment, construction, team, konshub).",
    add_completion=False,
    pretty_exceptions_show_locals=False,  # never risk a secret in a traceback
)


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    http: Annotated[
        bool, typer.Option("--http", help="Serve over streamable HTTP instead of stdio.")
    ] = False,
    host: Annotated[str, typer.Option(help="Bind host, with --http.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port, with --http.")] = 8000,
    path: Annotated[str, typer.Option(help="URL path, with --http.")] = "/mcp",
) -> None:
    """Run the clm-mcp server. Defaults to stdio; use `clm-mcp login` first
    if no credentials are configured yet."""
    if ctx.invoked_subcommand is not None:
        return  # a subcommand (e.g. `login`) handles its own logic instead

    settings = get_settings()
    configure_logging(settings.log_level)
    _validate_write_tools_or_exit(settings)
    server = build_server(settings)
    register_all(server, settings)

    if http:
        server.run(transport="streamable-http", host=host, port=port, streamable_http_path=path)
    else:
        server.run(transport="stdio")


def _validate_write_tools_or_exit(settings: Settings) -> None:
    """Fail fast with a clean CLI message on a bad CLM_WRITE_TOOLS value,
    rather than letting it surface later as an unhandled ValueError from
    deep inside tools/commands.py::register (which still raises the same
    check as a structural safety net — see config.py's write_tool_services).
    """
    try:
        settings.write_tool_services()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def login(
    refresh_token: Annotated[
        str | None,
        typer.Option(
            "--refresh-token",
            help="Store this refresh token directly, skipping the "
            "interactive username/password prompt.",
        ),
    ] = None,
) -> None:
    """Authenticate and store credentials for future `clm-mcp` runs.

    Without --refresh-token, prompts for a username and password on the
    terminal (the password is never echoed and never leaves this process —
    it is exchanged for a refresh token and discarded). Either way, the
    refresh token is validated against the identity service before being
    saved to ~/.config/clm-mcp/credentials.json (mode 0600).
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if refresh_token:
        # An unverified token supplied on the command line — confirm it
        # actually works before persisting it, or a typo would only surface
        # later as a confusing failure inside the MCP server itself.
        token = refresh_token
        username: str | None = None
        asyncio.run(_verify_refresh_token(settings, token))
    else:
        # No separate verification needed here: a successful password grant
        # (a 200 response carrying a fresh refresh_token) already *is* the
        # verification — hitting the identity endpoint a second time would
        # just be a redundant round-trip.
        try:
            username = input("CLM username (email): ").strip()
            password = getpass.getpass("CLM password: ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nAborted.", err=True)
            raise typer.Exit(code=1) from None
        token = asyncio.run(_password_grant(settings, username, password))

    save_credentials(settings.credentials_path, token, username)
    typer.echo(f"Credentials saved to {settings.credentials_path}")


async def _password_grant(settings: Settings, username: str, password: str) -> str:
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                settings.identity_token_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": settings.identity_origin,
                },
                data={"grant_type": "password", "username": username, "password": password},
            )
        except httpx.HTTPError as exc:
            typer.echo(f"Could not reach the identity service: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    if response.status_code != httpx.codes.OK:
        _print_token_error(response)
        raise typer.Exit(code=1)
    return TokenResponse.model_validate_json(response.content).refresh_token.get_secret_value()


async def _verify_refresh_token(settings: Settings, refresh_token: str) -> None:
    """Confirm `refresh_token` is actually valid before persisting it —
    saving a bad token would only surface as a confusing failure later,
    inside the MCP server itself."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                settings.identity_token_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": settings.identity_origin,
                },
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
        except httpx.HTTPError as exc:
            typer.echo(f"Could not reach the identity service: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    if response.status_code != httpx.codes.OK:
        _print_token_error(response)
        raise typer.Exit(code=1)


def _print_token_error(response: httpx.Response) -> None:
    try:
        error = TokenErrorResponse.model_validate_json(response.content)
        typer.echo(f"Login failed: {error.error_description or error.error}", err=True)
    except ValueError:
        typer.echo(f"Login failed: HTTP {response.status_code}", err=True)


def main() -> None:
    """Entry point referenced by `[project.scripts]` in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
