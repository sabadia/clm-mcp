"""Meta tools: session identity and enum discovery.

These are registered unconditionally (unlike the write-gated `*Command`
tools) since they're read-only and needed to use every other tool well:
`clm_whoami` confirms auth is working and supplies the caller's `site_id`
default; `clm_list_enums` surfaces the domain enums in `enums.py` so the
model can pass a valid status/type value even while their real names are
still `UNKNOWN_n` placeholders.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from clm_mcp.auth.errors import ClmAuthError
from clm_mcp.enums import DOMAIN_ENUM_SERVICES, DOMAIN_ENUMS
from clm_mcp.server import AppContext

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)


class WhoAmIResult(BaseModel):
    """The identity, site, and roles of the currently authenticated CLM user."""

    user_id: str
    display_name: str | None
    email: str | None
    site_id: str | None
    site_name: str | None
    tenant_id: str | None
    roles: list[str]
    token_expires_at: str


class EnumInfo(BaseModel):
    """One domain enum's known members and their int values."""

    name: str
    service: str
    members: dict[str, int]


def register(mcp: MCPServer[AppContext]) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def clm_whoami(ctx: Context[AppContext]) -> WhoAmIResult:
        """Return the identity, site, and roles of the authenticated CLM user.

        Call this first: it confirms authentication is actually working
        (triggering a token refresh if needed) and its `site_id` is the
        default most other tools use for their `site_id` parameter.
        """
        app = ctx.request_context.lifespan_context
        try:
            await app.token_manager.get_access_token()  # ensures claims are populated/fresh
        except ClmAuthError as exc:
            # TokenManager raises a plain ClmAuthError (missing/invalid
            # credentials, identity service down) — translate to ToolError
            # here too, or the MCP SDK reduces it to a useless "Error
            # executing tool clm_whoami" with the real reason discarded.
            raise ToolError(str(exc)) from exc
        claims = app.token_manager.claims
        if claims is None:
            raise ToolError(
                "No authenticated session (this should not happen once "
                "get_access_token() has succeeded)."
            )
        return WhoAmIResult(
            user_id=claims.user_id,
            display_name=claims.display_name,
            email=claims.email,
            site_id=claims.site_id,
            site_name=claims.site_name,
            tenant_id=claims.tenant_id,
            roles=claims.role,
            token_expires_at=claims.expires_at.isoformat(),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def clm_list_enums() -> list[EnumInfo]:
        """List CLM domain enums and their known integer values.

        Several API fields (e.g. shipment/lean-card status, severity) are
        undocumented integer enums in the spec — real member names are not
        yet known (see PLAN.md). Tools accepting one of these enums take
        either the int value shown here or its placeholder name (e.g.
        `"UNKNOWN_3"`) interchangeably. Currently shipment-only — the other
        three services have similar undocumented int fields but no
        confirmed value range yet (see enums.py's module docstring); use
        `clm_describe_operation` to see one of those fields' raw int type.
        """
        return [
            EnumInfo(
                name=name,
                service=DOMAIN_ENUM_SERVICES[name],
                members={member.name: member.value for member in enum_cls},
            )
            for name, enum_cls in DOMAIN_ENUMS.items()
        ]
