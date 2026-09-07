# clm-mcp

An [MCP](https://modelcontextprotocol.io) server exposing the SELISE **CLM Shipment**
REST API (`ClmShipmentWebService`) to MCP clients — Claude Code, Claude Desktop, Cursor,
or any other MCP-compatible tool.

- **Every one of the 121 non-`Test` operations in the spec is reachable, by default, with zero
  configuration** — 82 tools total:
  - **23 read-only tools** covering shipments, incidents, site equipment, lean cards
    (working packages), material handovers, cockpit dashboards, and weather.
  - **59 auto-generated write tools**, one per `*Command` operation (create/update/delete/
    discard/...), individually named and annotated by risk.
  - **A full-coverage gateway** (`clm_list_operations` / `clm_describe_operation` /
    `clm_invoke`) that reaches literally every operation — including the ~44 less-common query
    operations without a hand-written tool — generically, with pre-flight schema validation.
  - Set `CLM_ENABLE_WRITES=false` to opt **out** of the 59 write tools and run strictly
    read-only instead.
- Proactive token refresh for a **7-minute** access-token lifetime, correct handling of
  an API that returns HTTP 200 even for business failures, and response shaping so
  large DTOs don't flood the model's context.

---

## Requirements

- Python ≥ 3.13.3
- [uv](https://docs.astral.sh/uv/)
- A SELISE CLM account (username/password, or an existing refresh token)

## Install

```bash
git clone <this-repo>
cd clm-mcp
uv sync
```

## Authenticate

Pick **one** of these. If both are configured, environment variables win — the
on-disk store from `clm-mcp login` is only consulted when neither
`CLM_REFRESH_TOKEN` nor `CLM_USERNAME`+`CLM_PASSWORD` is set.

### Option A — `clm-mcp login` (recommended)

A one-time interactive login. Your password is typed at a `getpass` prompt (never
echoed, never sent anywhere except the identity service, and never passed as a tool
argument — see [Security](#security)). The resulting refresh token is validated and
saved to `~/.config/clm-mcp/credentials.json` (mode `0600`):

```bash
uv run clm-mcp login
# CLM username (email): you@example.com
# CLM password:
# Credentials saved to /home/you/.config/clm-mcp/credentials.json
```

Already have a refresh token (e.g. from your browser's network tab)? Skip the prompt:

```bash
uv run clm-mcp login --refresh-token <token>
```

### Option B — environment variables

Set one of these (e.g. in your MCP client's config, or a `.env` file — copy
`.env.example` to `.env` to start):

```bash
CLM_REFRESH_TOKEN=<token>
# — or —
CLM_USERNAME=you@example.com
CLM_PASSWORD=<password>
```

If none of the above is configured, every tool call fails with a clear
`ToolError` telling you to run `clm-mcp login`.

## Run

```bash
uv run clm-mcp                      # stdio — for MCP clients that spawn a subprocess
uv run clm-mcp --http --port 8000   # streamable HTTP — for a remote/containerized deployment
```

`--http` also accepts `--host` (default `127.0.0.1`) and `--path` (default `/mcp`).

### Add to Claude Code

```bash
claude mcp add clm -- uv run --project /path/to/clm-mcp clm-mcp
```

### Add to Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/clm-mcp", "clm-mcp"]
    }
  }
}
```

Credentials come from `clm-mcp login` automatically — no `env` block needed unless you
prefer `CLM_REFRESH_TOKEN`/`CLM_USERNAME`+`CLM_PASSWORD` inline.

**See [`USAGE.md`](USAGE.md) for a complete connection guide** covering Claude Code, Claude
Desktop, Cursor, Windsurf, VS Code (Copilot Chat), Cline, Zed, Continue.dev, and generic
stdio/HTTP MCP clients, plus operating guidance for coding agents using this server.

---

## Configuration reference

All variables use the `CLM_` prefix (see `.env.example` for the full, commented list).

| Variable | Default | Purpose |
|---|---|---|
| `CLM_REFRESH_TOKEN` | — | Refresh token (Option B) |
| `CLM_USERNAME` / `CLM_PASSWORD` | — | Password grant (Option B) |
| `CLM_CREDENTIALS_PATH` | `~/.config/clm-mcp/credentials.json` | Where `clm-mcp login` stores its token |
| `CLM_API_BASE_URL` | `https://msblocks.selisestage.com/api/business-clm-shipment` | Business API base |
| `CLM_IDENTITY_TOKEN_URL` | `https://clm.selisestage.com/api/identity/v25/identity/token` | Identity/token endpoint |
| `CLM_IDENTITY_ORIGIN` | `https://clm.selisestage.com/` | Required `Origin` header for the identity endpoint |
| `CLM_ENABLE_WRITES` | `true` | Register the 59 auto-generated write tools. Set to `false` to opt out and run read-only. |
| `CLM_TOKEN_REFRESH_SKEW_SECONDS` | `90` | Refresh this many seconds before actual expiry |
| `CLM_MAX_RESPONSE_BYTES` | `50000` | Byte cap before a list response is truncated |
| `CLM_LOG_LEVEL` | `INFO` | Logging level (always written to **stderr**, never stdout) |

---

## Tool reference

### Identity & discovery

| Tool | Description |
|---|---|
| `clm_whoami` | Identity, site, and roles of the authenticated user — call this first. |
| `clm_list_enums` | Known integer values for CLM's undocumented status/type enums. |

### Shipments

| Tool | Description |
|---|---|
| `clm_search_shipments` | Free-text shipment search, scoped to a site. |
| `clm_list_shipments` | Filtered, searched, ordered, paged shipment list (cockpit list view). |
| `clm_count_shipments` | Shipment counts by status bucket (open/approved/completed/cancelled). |
| `clm_get_shipment` | Full internal shipment record by id (up to 142 fields). |
| `clm_get_shipment_summary` | Smaller, external-facing shipment summary — prefer this over `clm_get_shipment` when you don't need every field. |
| `clm_get_shipment_comments` | Comments posted on a shipment. |
| `clm_get_shipment_event_logs` | Audit/event log history for a shipment or related entity. |
| `clm_get_shipment_timeline` | Day-wise timeline for a single shipment. |

### Incidents

| Tool | Description |
|---|---|
| `clm_list_incidents` | Non-deleted incidents/findings raised against a shipment. |
| `clm_get_incident` | Full detail of a single incident/finding. |

### Site equipment

| Tool | Description |
|---|---|
| `clm_list_equipment` | Bookable/non-bookable site equipment for the grid view. |
| `clm_list_equipment_timeline` | Equipment timeline entries (bookings), filtered and paged. |

### Lean cards (working packages)

| Tool | Description |
|---|---|
| `clm_list_working_packages` | Lean cards for a site, filtered by status. |
| `clm_count_working_packages` | Lean card counts by status (overdue/active/pending/completed). |

### Material handovers

| Tool | Description |
|---|---|
| `clm_list_material_handovers` | Handover summaries for a site, filtered by a **required** status string (the API rejects null/empty — see Known limitations) and date range. |
| `clm_get_material_handover` | Full handover detail, including event history and equipment usage. |

### Cockpit dashboard

| Tool | Description |
|---|---|
| `clm_get_cockpit_weekly_counts` | Weekly shipment count grid for a site's unloading zones. |
| `clm_get_weather` | Daily weather records for a site within a date range. |

### Full-coverage gateway

The curated tools above cover the common workflows. These three reach everything
else in the spec (~100 more read/write operations) at the cost of the caller
constructing its own request body:

| Tool | Description |
|---|---|
| `clm_list_operations` | List operations by tag and/or search — e.g. `tag="ShipmentCommand"`. |
| `clm_describe_operation` | Get an operation's resolved JSON Schema request/response shape. |
| `clm_invoke` | Execute any listed operation. Validates `params` against its schema before the HTTP call; refuses a `*Command` if the server was started with `CLM_ENABLE_WRITES=false`. |

`Test/*` operations (see [Security](#security)) are excluded from all three — they
never appear in `clm_list_operations` and `clm_invoke` refuses them by name.

### Write tools (on by default — set `CLM_ENABLE_WRITES=false` to disable)

One tool per `*Command` operation (59 total), named `clm_<tag>_<operation>` — e.g.
`clm_shipment_command_discard_shipment`, `clm_incident_command_create_incident`. Each
is generated from that operation's resolved request schema and annotated by its verb:

| Verb prefix | Annotation |
|---|---|
| `Create` | not destructive |
| `Update` / `Save` / `Upsert` | not destructive, idempotent |
| `Delete` / `Discard` | **destructive** |
| anything else (`Send`, `Generate`, `Complete`, `Change`, `Calculate`, `Upload`, …) | destructive, non-idempotent (conservative fallback — annotations are hints, not a guarantee, so an unrecognized verb is treated as risky rather than safe) |

A deeply nested field (an array of objects, say) falls back to a permissive JSON
object rather than a fully-typed nested schema — the API itself still validates it.
Use `clm_invoke` instead when you want full JSON-Schema pre-validation of a nested body.

To see the exact generated names, ask a connected client to list its tools, or run:

```bash
npx @modelcontextprotocol/inspector uv run clm-mcp
```

---

## Architecture

```
src/clm_mcp/
├── __main__.py        CLI: clm-mcp / clm-mcp login / clm-mcp --http
├── config.py           pydantic-settings, CLM_ env prefix
├── server.py           build_server(): MCPServer + typed lifespan
├── enums.py            domain enums (see Known limitations)
├── auth/               token lifecycle: models, on-disk store, TokenManager
├── api/                HTTP client (retry policy) + envelope unwrapping
├── spec/               vendored OpenAPI spec, operation registry, response shaping
├── services/           composition layer between tools and api/ — no business
│                       logic in tool functions
└── tools/              MCP tool registration: meta, curated domains, gateway,
                        auto-generated write tools
```

**Auth.** The identity service issues a **7-minute** access token. `TokenManager`
refreshes proactively (90s before expiry, configurable), coalesces concurrent
refreshes behind a single in-flight request, and forces a refresh on an unexpected
401. Refresh tokens observed on this API are not rotated, but the manager persists
whatever comes back regardless, in case that changes.

**Envelopes.** The business API returns **HTTP 200 even when the requested operation
failed** — success/failure lives in the JSON body (`IsSuccess`, `ErrorMessages`,
`ExternalError`, `ValidationErrors.IsValid`), not the status code. `api/envelope.py`
checks this on every response and raises a normal `ToolError` on failure, so the model
sees "shipment not found" the same way it sees any other tool outcome.

**Response shaping.** Some DTOs are large (`Shipment` has 142 properties, most
usually null for any given row). Every list-returning tool null-strips its result,
supports a `fields` parameter to project down to just what's needed, and enforces a
byte budget (`CLM_MAX_RESPONSE_BYTES`) — truncating rows (never a row's own fields)
with an explicit note rather than silently dropping data.

---

## Development

```bash
uv sync --group dev
uv run pytest              # full test suite
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src/clm_mcp/   # strict type check
uv run python scripts/refresh_spec.py         # check the vendored spec for drift
uv run python scripts/refresh_spec.py --write # ...and apply the update
```

```bash
npx @modelcontextprotocol/inspector uv run clm-mcp
```

opens the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) against
this server for interactive testing (`uv run mcp dev` isn't usable here since the
entry point is a Typer CLI, not a module-level `MCPServer` instance the Inspector's
`dev` command can import directly).

---

## Security

- **`clm-mcp login` is the only place a password is collected**, via a terminal
  `getpass` prompt — never as a tool argument. Per the MCP specification, "servers
  MUST NOT use elicitation to request sensitive information," so this server never
  asks the *client* to collect one either. Only a refresh token is ever written to
  disk.
- **Tokens are never logged.** A structlog processor redacts `access_token`,
  `refresh_token`, `password`, and `Authorization` from every log line, regardless of
  where in a nested structure they appear.
- **`Test/*` operations are permanently excluded** from this server (at the registry
  level — every tool, `clm_list_operations`, and `clm_invoke` all route through the
  same exclusion). `Test/GetUserData` on the upstream staging API returns
  super-admin credentials in plaintext to any authenticated caller; this is an
  upstream vulnerability that should be reported and fixed independently of this
  client — excluding it here only stops this server from being a vector for it, it
  does not remediate the underlying exposure.
- **Write tools are on by default** (every `*Command` operation gets a tool with zero
  setup) and individually annotated so an MCP client can prompt for confirmation before a
  destructive call — set `CLM_ENABLE_WRITES=false` for a connection that should never be
  able to mutate live data at all.

## Known limitations

- **Several domain enums have no documented names.** `ShipmentStatus` (13 values),
  `LeanCardStatus`, `Severity`, `ShipmentGroupingType`, `AccessType`, and
  `AvailableDateGetType` are exposed as bare integers in the OpenAPI spec with no
  labels. `enums.py` ships them as explicit `UNKNOWN_n` placeholders (not a guess —
  see that module's docstring) and `clm_list_enums` surfaces the known int ranges.
  Tools accept either the placeholder name or the raw int. Replace the placeholders
  with real names (from the CLM front-end source or a domain expert) when available.
- **The vendored spec's `servers[0].url` is wrong** (plain HTTP, missing the gateway
  path prefix) — this server hardcodes the verified-correct
  `CLM_API_BASE_URL` default instead of reading it from the spec.
- **No `securitySchemes` in the spec** — the Bearer scheme is inferred from testing
  against the live API, not documented. If the gateway ever requires an additional
  header, `api/client.py` is the one place to add it.
- **`clm_list_material_handovers`'s `status` is required**, despite the spec marking the
  underlying field `nullable: true` — the API rejects both a missing and an empty/null
  `Status` with `'Status' must not be empty.` `status` is matched as a literal value, not
  a wildcard: `All` is accepted without a validation error but matches zero handovers
  (confirmed live: `TotalCount=0` against a site with 35 real handovers). `InProgress` is
  confirmed live to filter correctly (returned all 35 matching handovers on that site).
  `Open`, `Completed`, `Pending`, `Draft`, and `Closed` are accepted without a validation
  error but their filtering semantics were not confirmed against real data.
- **Two upstream endpoints return a 500 instead of a clean business error**, reproduced
  identically via raw `curl` (not an artifact of this client): `LeanCardQuery/GetWorkingPackages`
  and `GetWorkingPackagesCount` (`Object reference not set to an instance of an object`), and
  `TimelineQuery/GetShipmentTimelineById` for a nonexistent shipment id (`Exception has been
  thrown by the target of an invocation`). `clm_list_working_packages`,
  `clm_count_working_packages`, and `clm_get_shipment_timeline` surface these faithfully as
  `ToolError`s — there is nothing to fix client-side.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `No CLM credentials found` | Run `clm-mcp login`, or set `CLM_REFRESH_TOKEN` / `CLM_USERNAME`+`CLM_PASSWORD`. |
| `Incorrect username or password.` | Check the credentials themselves against the CLM login page. |
| `The refresh token was rejected` | It expired or was revoked — run `clm-mcp login` again. |
| A tool call is missing/behaves oddly after an upstream API change | Run `scripts/refresh_spec.py` to check for spec drift, then grep `src/clm_mcp/tools/`/`services/` for the affected operation name before applying `--write`. |
| Response was truncated | The `note` field explains it — narrow your filters/page size, or pass `fields` to shrink each row. |
| Nothing happens over stdio / a client can't parse output | Something wrote to stdout directly — all logging in this project goes to stderr by design (`ruff`'s `T20` rule forbids `print()`); if you added code, check it didn't bypass that. |
