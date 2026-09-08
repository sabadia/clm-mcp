# clm-mcp

An [MCP](https://modelcontextprotocol.io) server exposing **four SELISE CLM business
services** — shipment, construction, team, and konshub — to MCP clients: Claude Code,
Claude Desktop, Cursor, or any other MCP-compatible tool.

- **Every one of the 364 non-`Test` operations across all four services is reachable, by
  default, with zero configuration** — 47 read-only tools by default:
  - **2 identity/discovery tools** (`clm_whoami`, `clm_list_enums`) plus **42 curated
    domain tools**: 18 covering shipment (shipments, incidents, site equipment, lean
    cards, material handovers, cockpit dashboards, weather), and 8 each for construction
    (materials, zones, site structure, wiki search), team (teams, members, join
    requests, invitations, contacts), and konshub (the warehouse/logistics dashboard:
    incoming/outgoing shipments, deliveries, storage, warehouse zones).
  - **A full-coverage gateway** (`clm_list_operations` / `clm_describe_operation` /
    `clm_invoke`) that reaches literally every operation in all four services —
    including all 181 write (`*Command`) operations and the 141 less-common query
    operations without a hand-written tool (322 operations total via the gateway) —
    generically, with pre-flight schema validation. **This is the default way to
    execute a write** — see below.
  - **181 auto-generated write tools exist but are opt-in**, one per `*Command`
    operation (create/update/delete/discard/...), individually named and annotated by
    risk. Registering all of them by default would add ~78,000 tokens of
    tool-definition context to *every request* — set `CLM_WRITE_TOOLS=shipment` (or a
    comma-separated list, or `all`) to opt specific services into named write tools.
  - Set `CLM_ENABLE_WRITES=false` to refuse every write outright — through `clm_invoke`
    *or* a named write tool — and run strictly read-only instead.
- Proactive token refresh for a **7-minute** access-token lifetime (shared across all
  four services — one login, one token), correct handling of an API that returns HTTP
  200 even for business failures, and response shaping so large DTOs don't flood the
  model's context.

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
| `CLM_API_ROOT_URL` | `https://msblocks.selisestage.com/api` | API root — each service's base is `{this}/business-clm-{shipment,construction,team,konshub}` |
| `CLM_API_BASE_URL` | — | **Deprecated**: shipment-only base URL override, kept for pre-multi-service configs. Equivalent to `CLM_API_BASE_URL_OVERRIDES='{"shipment": "..."}'`. |
| `CLM_API_BASE_URL_OVERRIDES` | `{}` | Per-service base URL overrides, as a JSON object: `{"konshub": "https://..."}` |
| `CLM_IDENTITY_TOKEN_URL` | `https://clm.selisestage.com/api/identity/v25/identity/token` | Identity/token endpoint (shared by all four services) |
| `CLM_IDENTITY_ORIGIN` | `https://clm.selisestage.com/` | Required `Origin` header for the identity endpoint |
| `CLM_ENABLE_WRITES` | `true` | Whether a `*Command` operation may execute at all, through `clm_invoke` **or** a named write tool. Set to `false` to refuse every write and run read-only. |
| `CLM_WRITE_TOOLS` | *(empty)* | Which services get a **named tool per `*Command` operation**, in addition to `clm_invoke` (which always works). Comma-separated slugs (`shipment`, `construction`, `team`, `konshub`) or `all`. Empty by default — see [Write tools](#write-tools). |
| `CLM_API_CONNECT_TIMEOUT_SECONDS` | `10` | TCP connect timeout for business API requests |
| `CLM_API_READ_TIMEOUT_SECONDS` | `60` | Read timeout for business API requests — some operations (e.g. `clm_get_material_usage`) take several seconds; raise this if you see timeouts rather than lowering it |
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

### Construction

| Tool | Description |
|---|---|
| `clm_list_materials` | Paginated, searchable material list for a site. |
| `clm_get_material` | A single material by its item id. |
| `clm_get_material_usage` | A material's shipment usage totals, grouped by status. |
| `clm_list_zones` | Zones belonging to a site or shared at the project level. |
| `clm_get_zone` | A zone by id, with equipment, way-points, and related areas. |
| `clm_list_site_locations` | A site's zones as locations, with direction way-points. |
| `clm_get_site_structure` | A site's structure (buildings/floors/laydowns), hierarchically. |
| `clm_search_wiki` | Free-text search over wiki page content. |

### Team

| Tool | Description |
|---|---|
| `clm_list_teams` | CLM teams for a site, own teams surfaced first by default. |
| `clm_list_team_members` | Members of (or unassigned candidates for) a team. |
| `clm_list_vendor_teams` | Vendor teams for a site. |
| `clm_list_join_requests` | Team join requests for a site, role-scoped to the caller. |
| `clm_get_join_request` | Full detail of a single team join request. |
| `clm_list_site_responsible_persons` | Responsible-person contacts for a site (e.g. shipment sender/recipient pickers). |
| `clm_get_person_info` | Extended profile combining Person/User/ClmTeamMember/temp-role data. |
| `clm_list_invitations` | Pending and past user invitations for a site. |

### KonsHub (warehouse/logistics dashboard)

| Tool | Description |
|---|---|
| `clm_list_konshub_incoming_shipments` | Incoming shipments in a date window, for the dashboard. |
| `clm_list_konshub_outgoing_shipments` | Outgoing shipments in a date window, for the dashboard. |
| `clm_list_konshub_deliveries` | Shipments for one tab of the Deliveries screen. |
| `clm_count_konshub_deliveries` | Deliveries-screen tab counts plus a grand total. |
| `clm_search_konshub_deliveries` | Free-text search over every Deliveries tab in one call. |
| `clm_get_konshub_shipment_comments` | Comments on a KonsHub shipment. |
| `clm_list_storage_zones` | Storage zones matching an optional combination of filters. |
| `clm_list_warehouse_zones` | Zones belonging to a warehouse (scoped by `warehouse_id`, not a site). |

*(KonsHub tool names carry a `konshub_` infix where they'd otherwise collide with a
shipment tool name — the shipment service already owns the unprefixed names.)*

### Full-coverage gateway

The curated tools above cover the common workflows across all four services. These
three reach everything else — including all 181 write operations — at the cost of the
caller constructing its own request body:

| Tool | Description |
|---|---|
| `clm_list_operations` | List operations, filterable by `service` (`shipment`/`construction`/`team`/`konshub`), `tag`, and/or free-text search. |
| `clm_describe_operation` | Get an operation's resolved JSON Schema request/response shape. |
| `clm_invoke` | Execute any listed operation — **the default way to run a write**. Validates `params` against its schema before the HTTP call; refuses a `*Command` if the server was started with `CLM_ENABLE_WRITES=false`. |

Operation names are `{tag}/{operation}` (e.g. `"ShipmentQuery/GetShipmentById"`) when
unambiguous across all four services — true for every operation today — or the fully
qualified `{service}/{tag}/{operation}` form always works
(`"konshub/KonsHubShipmentCommand/SplitKonsHubShipment"`).

`Test/*` operations (see [Security](#security)) are excluded from all three, in every
service — they never appear in `clm_list_operations` and `clm_invoke` refuses them by name.

### Write tools

**Opt-in, empty by default** — set `CLM_WRITE_TOOLS=shipment` (or a comma-separated list
of `shipment`/`construction`/`team`/`konshub`, or the literal `all`) to get a **named**
tool per `*Command` operation for that service, in addition to the always-available
`clm_invoke`. Registering all 181 by default would add ~78,000 tokens of
tool-definition context to every single request (measured: 59 shipment write tools
alone cost ~27,000 tokens) — `clm_invoke` is the default write path specifically to
avoid that, and is already schema-validated and gated by `CLM_ENABLE_WRITES` the same
way a named tool would be.

When enabled, each is named `clm_<tag>_<operation>` — e.g.
`clm_shipment_command_discard_shipment`, `clm_konshub_shipment_command_split_konshub_shipment` —
generated from that operation's resolved request schema and annotated by its verb:

| Verb prefix | Annotation |
|---|---|
| `Create` | not destructive |
| `Update` / `Save` / `Upsert` | not destructive, idempotent |
| `Delete` / `Discard` | **destructive** |
| anything else (`Send`, `Generate`, `Complete`, `Change`, `Calculate`, `Upload`, …) | destructive, non-idempotent (conservative fallback — annotations are hints, not a guarantee, so an unrecognized verb is treated as risky rather than safe) |

A deeply nested field (an array of objects, say) falls back to a permissive JSON
object rather than a fully-typed nested schema — the API itself still validates it.
Use `clm_invoke` instead when you want full JSON-Schema pre-validation of a nested body.

**`CLM_WRITE_TOOLS` and `CLM_ENABLE_WRITES` are independent settings**: the former
controls whether a *named tool exists*; the latter controls whether a write can
*execute at all* (through `clm_invoke` or a named tool). `CLM_ENABLE_WRITES=false`
always wins — there's no point in a named tool that would only ever refuse.

To see the exact generated names for a given `CLM_WRITE_TOOLS` value, ask a connected
client to list its tools, or run:

```bash
CLM_WRITE_TOOLS=all npx @modelcontextprotocol/inspector uv run clm-mcp
```

---

## Architecture

```
src/clm_mcp/
├── __main__.py         CLI: clm-mcp / clm-mcp login / clm-mcp --http
├── config.py            pydantic-settings, CLM_ env prefix, per-service base_url_for()
├── services_catalog.py  the 4 CLM services: slug, spec file, gateway segment, path prefix
├── server.py            build_server(): MCPServer + typed lifespan
├── enums.py             domain enums (see Known limitations)
├── auth/                token lifecycle: models, on-disk store, TokenManager (shared
│                        across all 4 services — one identity issuer)
├── api/                 HTTP client (retry policy, per-operation service routing) +
│                        envelope unwrapping
├── spec/
│   ├── specs/           vendored OpenAPI specs, one per service
│   ├── registry.py      merges all 4 specs into one operation index
│   └── shaping.py        response shaping (null-stripping, projection, truncation)
├── services/            composition layer between tools and api/ — no business
│                        logic in tool functions; one module per service
└── tools/               MCP tool registration: meta, curated domains (one module per
                         service), gateway, auto-generated write tools
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
uv run python scripts/refresh_spec.py                    # check all 4 vendored specs for drift
uv run python scripts/refresh_spec.py --write              # ...and apply the update, all 4
uv run python scripts/refresh_spec.py --service konshub     # just one service
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
- **`Test/*` operations are permanently excluded, in all four services** (at the
  registry level — every tool, `clm_list_operations`, and `clm_invoke` all route
  through the same exclusion). `Test/GetUserData` on the upstream staging API returns
  super-admin credentials in plaintext to any authenticated caller, **confirmed present
  in shipment, construction, team, and konshub alike**; this is an upstream
  vulnerability that should be reported and fixed independently of this client —
  excluding it here only stops this server from being a vector for it, it does not
  remediate the underlying exposure.
- **Writes execute only when `CLM_ENABLE_WRITES` is true** (the default) — through
  `clm_invoke` or a named write tool alike. Named write tools are themselves opt-in
  per service (`CLM_WRITE_TOOLS`, empty by default) and individually annotated so an
  MCP client can prompt for confirmation before a destructive call. Set
  `CLM_ENABLE_WRITES=false` for a connection that should never be able to mutate live
  data at all, regardless of `CLM_WRITE_TOOLS`.

## Known limitations

- **Several shipment-service domain enums have no documented names.** `ShipmentStatus`
  (13 values), `LeanCardStatus`, `Severity`, `ShipmentGroupingType`, `AccessType`, and
  `AvailableDateGetType` are exposed as bare integers in the OpenAPI spec with no
  labels. `enums.py` ships them as explicit `UNKNOWN_n` placeholders (not a guess —
  see that module's docstring) and `clm_list_enums` surfaces the known int ranges.
  Tools accept either the placeholder name or the raw int. Replace the placeholders
  with real names (from the CLM front-end source or a domain expert) when available.
  **Construction, team, and konshub have similar undocumented int fields** (e.g.
  konshub's `ApprovalStatus`/`CommissionedStatus`, team's `TeamType`/`RequestType`,
  construction's `LocationType`/`WKTType`) that are **not yet stubbed** — unlike
  `ShipmentStatus`, none of them have live-observed cardinality evidence yet, and
  guessing a range would be worse than leaving them as plain ints reachable via
  `clm_invoke`/`clm_describe_operation`.
- **Two of the four specs contain cyclic schemas** (konshub's `KonsHubShipment`
  self-references via `PreviousKonsHubShipments`; construction's `Reservation` /
  `ReservationObject` / `ReservationObjectSpecificDate` form a mutual cycle). The
  registry breaks a cycle with a permissive `{"type": "object"}` placeholder at the
  point it closes rather than failing to build — four operations
  (`KonsHubShipmentCommand/SplitKonsHubShipment` and three query operations) lose
  static pre-validation on the recursive field specifically; the API itself still
  validates the real shape at call time.
- **The vendored specs' `servers[0].url` is wrong in all four** (plain HTTP, missing
  the gateway path prefix) — this server derives each service's base URL from
  `CLM_API_ROOT_URL` instead of reading it from the spec.
- **No `securitySchemes` in any of the four specs** — the Bearer scheme is inferred
  from testing against the live API, not documented. If a gateway ever requires an
  additional header, `api/client.py` is the one place to add it.
- **Construction has inconsistent `SiteId` casing** across operations (`SiteId`,
  `siteId`, `Siteid`, `SiteIds`) — each curated construction tool uses the exact
  casing its own operation declares; don't assume `SiteId` blindly when using
  `clm_invoke` against a construction operation not covered by a curated tool.
- **`clm_list_material_handovers`'s `status` is required**, despite the spec marking the
  underlying field `nullable: true` — the API rejects both a missing and an empty/null
  `Status` with `'Status' must not be empty.` `status` is matched as a literal value, not
  a wildcard: `All` is accepted without a validation error but matches zero handovers
  (confirmed live: `TotalCount=0` against a site with 35 real handovers). `InProgress` is
  confirmed live to filter correctly (returned all 35 matching handovers on that site).
  `Open`, `Completed`, `Pending`, `Draft`, and `Closed` are accepted without a validation
  error but their filtering semantics were not confirmed against real data.
- **One specific staging site (the account's default/JWT-scoped site,
  `68BC0C11-963F-46CB-AF93-267B50ABCCAF`, "Scclm Team") has corrupted or incomplete data that
  makes several otherwise-healthy operations throw generic exceptions when scoped to it** —
  confirmed by re-running the *exact same operations* against a properly-configured site (e.g.
  "KonsHub Test Site 1") and getting clean, correct results both times. This was initially
  mistaken for universal upstream bugs during an earlier pass; a broader real-data test across
  multiple sites corrected that. Affected on that one site only:
  `LeanCardQuery/GetWorkingPackages`/`GetWorkingPackagesCount` (shipment),
  `ClmTeamQuery/GetTeamList` and `GetVendorTeams` (team — the latter's error message,
  `"Provided SiteId does not exists"`, really means "this site isn't vendor-team-scoped"),
  `KonsHubShipmentQuery/GetDashboardIncommingShipmentList`/`GetDashboardOutgoingShipmentList`
  (konshub), and `ZoneCommand/CreateZone` (construction, `"The site does not exist"` — this site
  isn't construction-zone-scoped). If a query in one of these services fails ambiguously, try a
  different `site_id` before concluding the API itself is broken.
- **`TimelineQuery/GetShipmentTimelineById` returns a 500 instead of a clean 404** for a
  genuinely nonexistent shipment id — confirmed independent of which site is used, reproduced
  via raw `curl`. `clm_get_shipment_timeline` surfaces this faithfully as a `ToolError`.
- **`ClmTeamQuery/GetSiteResponsiblePersons`'s `TotalCount` does not track its `Data` array
  length at all** (observed both `TotalCount: 1` with 0 real rows, and `TotalCount: 1` with 7
  real rows), confirmed live on more than one site — a genuine API defect, not a client bug.
  Treat this operation's `total_count` as unreliable; trust `returned`/`data` instead.
  `clm_list_site_responsible_persons` is affected.
- **`ConstructionManagementQuery/GetMaterialUsagesById` always reports `IsSuccess: false`, even
  on a genuine successful lookup** — confirmed live with two different real `MaterialId`s on two
  different sites (and independently by the user with their own payload): `Data` is populated
  and correct despite the false flag, and only a truly nonexistent `MaterialId` returns
  `Data: null`. **Handled transparently**: `api/envelope.py` special-cases exactly this one
  operation (by its canonical name) to treat a populated `Data` as success regardless of the
  flag, so `clm_get_material_usage` works normally — a null `Data` is still correctly treated as
  failure. This operation is also noticeably slow (observed 3–12s) — worth knowing if you're
  tuning `CLM_API_READ_TIMEOUT_SECONDS` down from its 60s default.
- **Team and konshub curated list tools, plus `clm_list_materials` (construction), use
  one-based pagination** (`page_number=1` is the first page) — unlike shipment/most other
  construction tools' zero-based convention. Confirmed live: `GetJoinRequestList`, `GetTeamList`,
  `GetMaterialList`, and `GetDeliveriesTabShipmentList` all compute
  `skip = (PageNumber - 1) * PageSize` internally and reject `PageNumber=0` with a 500. Each
  curated tool's own `page_number` default and description already reflect the correct
  convention for that operation — this only matters if you override `page_number` explicitly.
- **`clm_get_cockpit_weekly_counts` rejects a date window wider than 92 days.** The underlying
  API returns one entry per *day* with no size cap of its own — confirmed live, a 2020–2027
  window returned ~530,000 bytes (over 10x `CLM_MAX_RESPONSE_BYTES`'s default) with zero
  truncation, since it's an object response rather than a list one. The 92-day cap is enforced
  before the HTTP call, with a clear `ToolError` telling the caller to narrow the range.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `No CLM credentials found` | Run `clm-mcp login`, or set `CLM_REFRESH_TOKEN` / `CLM_USERNAME`+`CLM_PASSWORD`. |
| A `clm_<tag>_command_<op>` write tool is missing | `CLM_WRITE_TOOLS` doesn't include that service (empty by default — see [Write tools](#write-tools)). The write still works via `clm_invoke`; set `CLM_WRITE_TOOLS=<service>` if you want it named too. |
| `clm_invoke` refuses a write with "CLM_ENABLE_WRITES=false" | That's the real read-only gate — `CLM_WRITE_TOOLS` only controls whether a *named* tool exists, it never gates execution. |
| `Incorrect username or password.` | Check the credentials themselves against the CLM login page. |
| `The refresh token was rejected` | It expired or was revoked — run `clm-mcp login` again. |
| A tool call is missing/behaves oddly after an upstream API change | Run `scripts/refresh_spec.py` to check for spec drift, then grep `src/clm_mcp/tools/`/`services/` for the affected operation name before applying `--write`. |
| Response was truncated | The `note` field explains it — narrow your filters/page size, or pass `fields` to shrink each row. |
| Nothing happens over stdio / a client can't parse output | Something wrote to stdout directly — all logging in this project goes to stderr by design (`ruff`'s `T20` rule forbids `print()`); if you added code, check it didn't bypass that. |
