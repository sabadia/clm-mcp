# CLM Shipment MCP Server — Implementation Plan

## Context

`/Users/mahamudul/work/office/clm-mcp` is currently a bare `uv` scaffold (`hello.py`, empty
`pyproject.toml` dependencies, no source layout). We are building a production-grade MCP server
that exposes the **ClmShipmentWebService** REST API to MCP clients (Claude Code, Claude Desktop,
Cursor, and remote HTTP consumers).

The upstream API is a CQRS-style .NET service documented at
`https://msblocks.selisestage.com/api/business-clm-shipment/swagger/v1/swagger.json`
(OpenAPI 3.0.1, 124 operations, 343 schemas). Authentication is a SELISE Blocks identity service
issuing short-lived JWTs.

The outcome is a `clm-mcp` package that lets an LLM search, inspect, and (opt-in) mutate CLM
shipments, incidents, site equipment, lean cards, material handovers and timelines — with correct
token lifecycle handling, a context-efficient tool surface, and safe defaults.

---

## Verified facts (measured live during planning — do not re-derive)

**Spec shape**
| Fact | Value |
|---|---|
| Operations | 124 (120 `POST`, 4 `GET`) |
| Schemas | 343, **acyclic**, shallow (median request closure = 1 schema) |
| `operationId` | **absent** — tool names must be derived from the URL path |
| `securitySchemes` | **absent** — auth is undocumented; Bearer confirmed by experiment |
| Tags | 20, split `*Query` (71 ops) / `*Command` (50 ops) / `Test` (3 ops) |
| Operation `summary` | present and high quality — reuse verbatim as tool descriptions |

**⚠ The spec's `servers[0].url` is `http://msblocks.selisestage.com/` and is wrong** — it is
plaintext HTTP and omits the `/api/business-clm-shipment` gateway prefix. The correct base is
`https://msblocks.selisestage.com/api/business-clm-shipment`. Hardcode/configure this; never read
it from the spec.

**Auth (verified against the live staging identity service)**
| Fact | Value | Consequence |
|---|---|---|
| `expires_in` | **420 s (7 min)** | proactive refresh is mandatory |
| Refresh token | 32-char opaque, **not rotated** (same value returned on refresh) | persist once, reuse; still re-store defensively |
| `Origin` header | **required** — omitting it → `400 http_origin_or_referer_header_is_require` | always send `Origin: https://clm.selisestage.com/` |
| Bad password | `400 {"error":"incorrect_user_name_or_password"}` | map to a distinct exception |
| Bad refresh token | `400 {"error":"invalid_grant"}` | trigger password-grant fallback |
| JWT claims | `tenant_id`, `site_id`, `user_id`, `display_name`, `site_name`, `role[]`, `exp` | **auto-fill the `SiteId` parameter most queries require** |
| Business API unauthenticated | `401` | auth is genuinely enforced |

**Response envelopes** — the API returns **HTTP 200 even for business failures**. Errors live in
the body and must be read from there:
- Query: `{Data, IsSuccess, StatusCode, ErrorMessage, PropertyName, ValidationErrors, TotalCount}`
- Command: `{RequestUri, ExternalError, HttpStatusCode, Errors, ErrorMessages, StatusCode}`

**Response bloat** — `Shipment` has 142 properties, `CockpitListViewShipmentDto` 41,
`ShipmentTimelineModel` 102. Unshaped list responses will flood the model's context. Response
projection and truncation are load-bearing, not nice-to-have.

**🔴 Upstream security defect (exclude, and report to the API team)**
`GET /ClmShipmentWebService/Test/GetUserData` returns super-admin credentials
(`SuperAdminUserEmail`, `SuperAdminUserCredential`) in plaintext to **any authenticated caller**.
All three `Test/*` operations are excluded from this server at the registry level — they are
filtered out of the curated tools, the gateway listing, **and** `clm_invoke`.

---

## Approved design decisions

1. **Tool surface — hybrid.** ~19 curated, typed, well-documented tools for the common workflows,
   plus a 3-tool gateway (`clm_list_operations` / `clm_describe_operation` / `clm_invoke`) giving
   full coverage of the remaining ~100 operations. Keeps tool-definition context to ~6 KB.
2. **Credentials — env/config + a `clm-mcp login` CLI subcommand.** The MCP spec states servers
   **MUST NOT** use elicitation to request sensitive information, so passwords are collected only
   on the terminal via `getpass`, exchanged for a refresh token, and stored at
   `~/.config/clm-mcp/credentials.json` (mode `0600`). No tool ever takes a password as an argument.
3. **Writes — read-only by default.** Only `*Query` operations register unless
   `CLM_ENABLE_WRITES=true`. All tools carry correct `ToolAnnotations`.
4. **Transport — stdio (default) and streamable HTTP** behind `--http`, from one `build_server()`
   factory.
5. **Enums — stub map with TODOs.** `ShipmentStatus` (0–12) et al. have no names in the spec; ship
   `enums.py` with placeholders + a `clm_list_enums` tool, accepting names or ints.

---

## Stack

`mcp[cli]>=2.1.1` (high-level `MCPServer` from `mcp.server`; `FastMCP` was renamed in 2.x),
`httpx`, `pydantic>=2`, `pydantic-settings`, `structlog`, `jsonschema`.
Dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`. Python 3.13 (already pinned).

---

## Layout

```
clm-mcp/
├── PLAN.md                      # copy of this file (repo-local, per global CLAUDE.md)
├── pyproject.toml               # deps, [project.scripts] clm-mcp, ruff/mypy/pytest config
├── README.md                    # setup, auth, client config, tool reference, troubleshooting
├── .env.example
├── src/clm_mcp/
│   ├── __main__.py              # CLI: `clm-mcp`, `clm-mcp login`, `--http --port`
│   ├── config.py                # pydantic-settings Settings (CLM_* env prefix)
│   ├── logging.py               # structlog → STDERR, with a token-redaction processor
│   ├── enums.py                 # ShipmentStatus etc. + TODO placeholders
│   ├── server.py                # build_server(): MCPServer + typed lifespan
│   ├── auth/
│   │   ├── models.py            # TokenResponse, JwtClaims, StoredCredentials
│   │   ├── store.py             # ~/.config/clm-mcp/credentials.json, 0600
│   │   ├── token_manager.py     # single-flight proactive refresh
│   │   └── errors.py
│   ├── api/
│   │   ├── client.py            # ClmApiClient: httpx.AsyncClient + auth + retry
│   │   ├── envelope.py          # unwrap Query/Command envelopes → ToolError
│   │   └── errors.py
│   ├── spec/
│   │   ├── swagger.json         # vendored spec (582 KB)
│   │   ├── registry.py          # OperationRegistry: parse, $ref-resolve, index, exclude Test/*
│   │   └── shaping.py           # null-stripping, field projection, byte-cap truncation
│   ├── services/                # composition layer — no business logic in tool functions
│   │   ├── shipments.py  incidents.py  equipment.py
│   │   └── lean_cards.py  handovers.py  cockpit.py
│   └── tools/
│       ├── __init__.py          # register_all(mcp, settings)
│       ├── shipments.py  incidents.py  equipment.py
│       ├── lean_cards.py  handovers.py  cockpit.py
│       ├── meta.py              # clm_whoami, clm_list_enums
│       └── gateway.py           # list/describe/invoke
├── scripts/refresh_spec.py      # re-download spec, diff operations, report drift
└── tests/                       # unit + in-process MCP Client integration tests
```

---

## Key implementation details

### `auth/token_manager.py` — the crux (7-minute tokens)

- Track expiry with **`time.monotonic()`** against `expires_in`, not wall-clock (immune to clock
  skew); cross-check against the JWT `exp` claim and take the earlier.
- Refresh when `remaining < CLM_TOKEN_REFRESH_SKEW_SECONDS` (default **90 s**, ~21 % of the TTL).
- **Single-flight**: an `asyncio.Lock` so N concurrent tool calls cause exactly one refresh, with
  a double-check of validity after acquiring the lock.
- Credential resolution order: `CLM_REFRESH_TOKEN` → `CLM_USERNAME`+`CLM_PASSWORD` →
  `~/.config/clm-mcp/credentials.json` → `ToolError("run: clm-mcp login")`.
- On `invalid_grant` during refresh, fall back to the password grant if credentials are available;
  otherwise surface the "run `clm-mcp login`" error.
- Always send `Origin: https://clm.selisestage.com/` (required — see verified facts).
- Persist whatever `refresh_token` comes back even though rotation is currently off, so the client
  keeps working if the server enables rotation later.
- **Never log token values.** A structlog processor redacts `access_token`, `refresh_token`,
  `password`, and `Authorization` anywhere in the event dict.
- Parse the JWT payload for claims **without signature verification** — it is used only for
  convenience defaults (`site_id`) and `clm_whoami` display, never for an authorization decision.
  Document this explicitly in the module docstring.

### `api/client.py`

- One `httpx.AsyncClient` created in the server lifespan (connection reuse matters at a 7-min token
  TTL), with explicit timeouts (connect 10 s / read 60 s) and connection limits.
- Retry policy, deliberately asymmetric:
  - `401` → force-refresh the token, retry **once** (safe for commands: a 401 means the request
    never reached business logic).
  - `429`/`5xx` → exponential backoff with jitter, max 3 attempts, **for `*Query` operations only**.
  - Network timeouts on `*Command` operations are **never** retried (non-idempotent).
- Delegate envelope unwrapping to `api/envelope.py`.

### `api/envelope.py`

Map `IsSuccess == false`, non-empty `ErrorMessages`, or `ValidationErrors.IsValid == false` onto
`ToolError` (`from mcp.server.mcpserver.exceptions import ToolError`) carrying the server's own
message plus `PropertyName` when present. Never let a business failure look like success just
because the HTTP status was 200.

### `spec/registry.py`

Load the vendored `swagger.json` **once at import**, and build a compact in-memory index:
`name → {method, path, tag, summary, request_schema (fully $ref-resolved), response_schema}`.
The schema graph is acyclic (verified), so resolution is a simple recursive inline with a memo —
no cycle guard needed, but assert acyclicity at build time so a future spec change fails loudly.
Filter `Test/*` here so exclusion cannot be bypassed.

### `spec/shaping.py`

1. Strip null-valued keys by default (the 142-property DTOs are mostly null — the single biggest win).
2. Optional `fields: list[str]` projection on every list-returning tool.
3. Byte cap `CLM_MAX_RESPONSE_BYTES` (default 50 000). On overflow, truncate the `Data` array and
   return an explicit note telling the model to page or project rather than silently dropping rows.
4. Return structured output: `{data, total_count, returned, truncated, note}`.

### `tools/` — the curated ~19

`clm_whoami`, `clm_list_enums`, `clm_search_shipments`, `clm_list_shipments`,
`clm_count_shipments`, `clm_get_shipment`, `clm_get_shipment_summary` (the far smaller
`GetShipmentForExternal` DTO — prefer this by default), `clm_get_shipment_comments`,
`clm_get_shipment_event_logs`, `clm_get_shipment_timeline`, `clm_list_incidents`,
`clm_get_incident`, `clm_list_equipment`, `clm_list_equipment_timeline`,
`clm_list_working_packages`, `clm_count_working_packages`, `clm_list_material_handovers`,
`clm_get_material_handover`, `clm_get_cockpit_weekly_counts`, `clm_get_weather`.

Each: a Pydantic v2 input model, `SiteId` defaulting to the JWT `site_id` claim, and
`ToolAnnotations(read_only_hint=True, idempotent_hint=True)`. Tool functions stay thin — parameter
mapping only; composition lives in `services/`.

### `tools/gateway.py`

- `clm_list_operations(tag=None, search=None, limit=50)` → name, method, tag, one-line summary.
- `clm_describe_operation(operation)` → summary + resolved request JSON Schema + response shape.
- `clm_invoke(operation, params)` → validate `params` against the resolved schema with `jsonschema`
  **before** the HTTP call, then execute. Rejects `Test/*` always, and `*Command` operations unless
  `CLM_ENABLE_WRITES=true`.

### Write-mode annotations (when `CLM_ENABLE_WRITES=true`)

`Get*` → `read_only_hint=True, idempotent_hint=True`; `Create*` → `destructive_hint=False`;
`Update*`/`Save*`/`Upsert*` → `idempotent_hint=True`; `Delete*`/`Discard*` → `destructive_hint=True`.

### `logging.py` — stdio hazard

Under stdio transport, **stdout is the JSON-RPC channel**. structlog must be configured to write to
**stderr** only. Any stray `print()` corrupts the protocol; enforce with a ruff rule (`T20`).

---

## Task checklist

- [x] 1. Copy this plan to repo-local `PLAN.md`; scaffold `pyproject.toml` (deps, `[project.scripts]
      clm-mcp = "clm_mcp.__main__:main"`, ruff/mypy/pytest config), `src/` layout, `.env.example`;
      delete `hello.py`.
- [x] 2. `config.py` (pydantic-settings, `CLM_` prefix) + `logging.py` (structlog → stderr, token
      redaction processor).
- [x] 3. `auth/models.py`, `auth/errors.py`, `auth/store.py` (0600 credential file).
- [x] 4. `auth/token_manager.py` — proactive single-flight refresh. **Unit tests with a fake clock
      covering: expiry-with-skew, concurrent single-flight, `invalid_grant` → password fallback,
      missing-`Origin` regression.**
- [x] 5. Vendor `swagger.json`; build `spec/registry.py` (+ acyclicity assertion, `Test/*` exclusion)
      and `scripts/refresh_spec.py`.
- [x] 6. `api/envelope.py` + `api/errors.py` with tests for both envelope shapes and the HTTP-200
      business-failure case.
- [x] 7. `api/client.py` — auth injection, asymmetric retry policy. Tests with `respx`.
- [x] 8. `spec/shaping.py` — null-stripping, projection, byte-cap truncation. Tests.
- [x] 9. `server.py` — `build_server()` + typed lifespan owning the `httpx` client and token manager.
- [x] 10. `enums.py` + `tools/meta.py` (`clm_whoami`, `clm_list_enums`).
- [x] 11. `services/` + curated read-only tools across shipments / incidents / equipment /
      lean cards / handovers / cockpit.
- [x] 12. `tools/gateway.py` — list / describe / invoke with pre-flight `jsonschema` validation.
- [x] 13. Write-mode registration behind `CLM_ENABLE_WRITES` with correct annotations.
- [x] 14. `__main__.py` — `clm-mcp` (stdio), `clm-mcp --http --port N`, `clm-mcp login`
      (`getpass`, `--refresh-token`).
- [x] 15. Integration tests using the SDK's in-process `Client(mcp)` against a mocked API.
- [x] 16. `README.md` — setup, auth flows, client config snippets, full tool reference,
      troubleshooting, and the `Test/GetUserData` security note.
- [x] 17. Run `ruff format`, `ruff check`, `mypy --strict`, `pytest`. Fix all findings.

---

## Verification

**Automated** — `ruff format --check`, `ruff check`, `mypy --strict src/`, `pytest -q` (target:
token manager, envelope, shaping, and registry fully covered; the network layer mocked with `respx`).

**Live smoke test** (staging credentials are known-good; note the account's `site_id`
`68BC0C11-…` currently returns **zero shipments**, so an empty `Data` array is expected and is
*not* a failure):
```bash
uv run clm-mcp login --refresh-token <token>     # or interactive
uv run python -c "..."                            # exercise clm_whoami → expect Super Admin / roles
```

**MCP Inspector** — `uv run mcp dev src/clm_mcp/__main__.py`; confirm the tool list is ~22 entries
in read-only mode, that `clm_describe_operation` returns a resolved schema, and that
`clm_invoke("Test/GetUserData", {})` is **refused**.

**Token-lifecycle test** — the real proof that the 7-minute TTL is handled: call a tool, wait
> 420 s, call again, and confirm from the structlog output that exactly one refresh occurred and
the second call succeeded without re-authentication.

**Claude Code end-to-end** — register via `claude mcp add`, then ask a natural-language question
("count shipments for my site") and confirm correct tool selection and a shaped response.

---

## Open items / risks

- **`ShipmentStatus` 0–12 names are unknown.** Shipped as TODO placeholders; the model will not be
  able to filter by status meaningfully until you supply the real labels from the CLM source.
- **`Test/GetUserData` leaks super-admin credentials** on the live staging service. Excluded here,
  but it needs reporting and rotation upstream — exclusion in this client does not fix the exposure.
- **No `securitySchemes` in the spec**, so the Bearer scheme is inferred from experiment. If the
  gateway later requires a tenant header (e.g. `x-blocks-key`), `api/client.py` is the single place
  to add it.
- **The staging account has no shipment data**, so curated tools cannot be validated against real
  payloads. Response shaping is therefore tested against schema-derived fixtures; expect to revisit
  field projection once real data is reachable.
- **A 7-minute TTL under stdio** means a long-idle client will always refresh on its next call.
  That is handled, but it makes the token manager the highest-risk component — hence the fake-clock
  test suite in task 4.

---

## Revision pass (live E2E against the real staging API, post-implementation)

A full revision pass compared every module against this plan and exercised all 23 read-only
tools plus a spot check of the auto-generated write tools against the live staging API
(`aardo@yopmail.com`). No crashes occurred anywhere; every failure was either a correctly
surfaced business error or a pre-existing upstream defect (documented below). Five real gaps
between this plan's stated design and the actual implementation were found and fixed:

1. **Auto-generated write tools crashed on any `date-time` field** (`tools/commands.py`).
   `params.model_dump(exclude_none=True)` left `datetime` objects unconverted, and httpx's
   `json=` cannot serialize those — every write tool with a date field (e.g.
   `UpsertAdhocShipment`) failed with an unhandled `TypeError`, not a `ToolError`. Fixed with
   `mode="json"` on the dump. Regression test:
   `test_generated_tool_with_datetime_field_serializes_correctly`.
2. **Auth failures were silently swallowed everywhere.** `auth/errors.py`'s own docstring always
   said tool-facing code "should catch these and translate them into a `ToolError`" — but no call
   site ever did. `TokenManager.get_access_token()`/`force_refresh()` raise a plain `ClmAuthError`
   (e.g. `MissingCredentialsError`, with the crucial "run `clm-mcp login`" message), and the MCP
   SDK reduces any non-`ToolError` exception raised inside a tool to a bare, useless
   "Error executing tool X" — discarding the message entirely. This affected **every single
   tool**, including the most important first-run message. Fixed at all three call sites that
   invoke `TokenManager` directly: `api/client.py::_fetch_body`, `services/common.py::resolve_site_id`,
   `tools/meta.py::clm_whoami`. Regression tests: `tests/test_auth_error_propagation.py` (6 tests,
   one per tool path).
3. **A bad enum name had the same swallowing bug.** `enums.resolve_enum_value` correctly raises a
   `ValueError` with a helpful "valid names are..." message, but nothing translated it to
   `ToolError` at the tool-facing call site either. Fixed by adding
   `services/common.py::resolve_enum_param`, a `ToolError`-safe wrapper every enum-typed curated
   parameter must go through. Regression test:
   `test_list_working_packages_bad_enum_name_reports_helpful_tool_error`.
4. **`ShipmentQuery/GetShipmentsForListView` 500s on an explicit null `OrderByField`** — verified
   live: sending `"OrderByField": null` returns `500 Value cannot be null. (Parameter 'key')`,
   but *omitting* the key entirely succeeds (the .NET side has a non-null default the JSON
   deserializer only overwrites on an explicit `null`, never on a missing key). This broke
   `clm_list_shipments` in its default (no `order_by_field`) form — a core curated tool.
   Fixed by adding `services/common.py::_compact`, applied inside `call_list`/`call_object`
   (used only by the curated read-only layer, never `clm_invoke` or the generated write tools,
   both of which must preserve exactly what the caller sent). Regression test:
   `test_list_shipments_omits_unset_optional_fields_from_request_body`.
5. **`MaterialHandoverQuery/GetMaterialHandoverList` requires a non-empty `Status`** despite the
   spec marking it `nullable: true` — verified live that both an omitted and an explicit
   null/empty `Status` are rejected (`'Status' must not be empty.`), while `clm_list_material_handovers`
   had defaulted it to `None`. Made `status` a required tool parameter; the docstring lists
   string values confirmed live to be accepted (`Open`, `Completed`, `InProgress`, `Pending`,
   `Draft`, `Closed`, `All`) without confirming their filtering semantics (no handover data
   existed on the test account to check against). Regression test:
   `test_list_material_handovers_requires_status`.

Two smaller, non-bug fixes: `_build_error_message` was dumping `ValidationErrors.Errors` entries
(FluentValidation `ValidationFailure` objects) as raw Python `repr()` instead of extracting their
`ErrorMessage` field — fixed with `_format_validation_error`, regression test
`test_validation_error_object_is_formatted_readably`. `call_object` reported a nonexistent
get-by-id result ("`Data: null`, `IsSuccess: true`" — observed live for `GetShipmentById`) as
"expected an object response but got NoneType", which reads like an internal type error; now
reports "no result found for the given parameters." `__main__.py`'s interactive login also made
a redundant extra identity-service round-trip to "verify" a token it had just received from a
successful password grant; removed (the `--refresh-token` path still verifies, since that token
is unverified input).

**Additional upstream defects observed live (not fixable client-side — excluded/documented, not
silently masked):**
- `LeanCardQuery/GetWorkingPackages` and `GetWorkingPackagesCount` return `500 Object reference
  not set to an instance of an object` on this test account (reproduced identically via raw
  curl — confirmed not a client-side artifact).
- `TimelineQuery/GetShipmentTimelineById` returns `500 Exception has been thrown by the target of
  an invocation` for a nonexistent (even well-formed GUID) shipment id, instead of a clean
  not-found business error.

All 103 tests pass (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) after this
pass; see `USAGE.md` for the end-user connection guide across MCP clients.

---

## Full-coverage & robustness pass (user-directed, post-revision)

Prompted by a direct question: "why only 23 read tools — what about write/update/delete for every
endpoint?" Verified and clarified with the user (see decisions below) that coverage was never
actually incomplete — `clm_invoke` already reaches all 121 non-`Test` operations, and all 59
write tools already existed, gated behind `CLM_ENABLE_WRITES`. Confirmed by direct test: every one
of the 121 operations' schemas is resolvable, JSON-serializable, and validatable with zero
exceptions (a hard proof of "no real gap," not an assumption).

**Decisions (confirmed with the user):**
1. **Query coverage**: keep `clm_invoke` as the fallback for the ~44 uncurated query operations —
   do not expand the always-visible tool list to full 1:1 (the user was satisfied once reachability
   was proven, matching the original Hybrid design's own rationale).
2. **Write tools**: flip to **default-on**. The user picked the "no `CLM_ENABLE_WRITES` flag
   needed" option; implemented instead as `enable_writes` defaulting to `True` with the same
   setting kept as an explicit **opt-out** (`CLM_ENABLE_WRITES=false` for read-only mode) — a
   judgment call to preserve a safety valve without contradicting "default-on" (the day-to-day
   behavior the user asked for is unchanged: writes just work with no setup). Flagged to the user
   for correction if they want the setting removed outright instead.
3. **Token/corner-case testing**: fake-clock unit tests only (no live 7-minute wait added to the
   regular suite) — expand corner-case coverage there instead.

**Tasks:**
- [x] A. Flip `Settings.enable_writes` default to `True` in `config.py`; update `.env.example` and
      inline comments to describe it as an opt-out.
- [x] B. Update `README.md` and `USAGE.md`: tool-count claims ("23 read-only tools"), the write-tools
      section, and the operating-guidance note about writes being off by default all need
      correcting to reflect default-on + opt-out.
- [x] C. Add `tests/test_full_coverage.py`: a structural test asserting every one of the 121
      registered operations (a) has a non-None resolved description via the registry, (b) is
      JSON-schema-valid, and (c) is reachable through `clm_invoke`'s validation path — the
      automated, permanent version of the manual check just run. Also assert the total counts
      (62 query + 59 command = 121) so a future spec change that silently drops an operation is
      caught.
- [x] D. Expand `tests/test_token_manager.py` with corner cases: exact skew-boundary (remaining
      == skew), a malformed/undecodable JWT, `IdentityServiceError` (identity host unreachable)
      recovering cleanly on the next call rather than corrupting state, refresh-token rotation
      (server returns a *different* refresh token) being picked up and persisted, and no
      deadlock/corruption when `force_refresh()` (401-triggered) races a concurrent proactive
      `get_access_token()`.
- [x] E. Add `tests/test_api_client.py` cases for auth/permission corner cases: a `403 Forbidden`
      (a real permissions error, distinct from `401`) must surface immediately as a `ToolError`
      without triggering a token refresh or a retry loop.
- [x] F. Full verification sweep (`ruff format`, `ruff check`, `mypy --strict`, `pytest`) and a
      final live smoke test confirming write tools are now visible without setting any env var.

**Bonus fix found while writing task D**: `_decode_claims`'s JWT decode/validation failure was
the same "swallowed by the MCP SDK" bug class as the earlier auth-error-propagation fixes — a
malformed or claims-incomplete access token from the identity service raised a raw
`jwt.PyJWTError`/`pydantic.ValidationError`, neither a `ClmAuthError`, so none of the three
translation call sites would catch it. Wrapped in `_store_active_token` to raise
`IdentityServiceError` instead. Regression tests:
`test_malformed_access_token_raises_identity_service_error`,
`test_access_token_missing_required_claims_raises_identity_service_error`.

**Final state**: 477 tests pass (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`)
from a frozen fresh install. Live-verified with the user's credentials: 82 tools with zero
configuration (23 read + 59 write), `clm_whoami` succeeds, and `clm_describe_operation` succeeds
even for the one write operation with an empty spec `summary`.

## Real-data E2E pass (user-directed, post-full-coverage)

User asked for a full live end-to-end test of all 59 write operations against the real API with
genuine business data (not synthetic UUIDs), followed by cleanup. The account's own JWT-scoped
site had zero configured reference data (no teams/materials/zones/equipment types), which blocked
writes that depend on them. The user pushed to try harder rather than accept the limitation and
supplied a real working `curl` example for `SiteEquipmentCommand/CreateOrUpdateEquipment`
(namespaced i18n keys like `"TENANT.EQUIPMENT.UNLOADING"` and a different, fully-configured site
id) — the key unblocking discovery. With that, ran a complete real-data shipment lifecycle (create
→ comment → incident → sustainability address → PDF → material handover → status-update-attempt →
discard) through the MCP server itself, verified each step by re-querying (not by trusting a 200),
and included the 3 email-sending commands on explicit go-ahead. Cleanup confirmed complete via
`DeleteDraftShipment`'s rejection (`"Shipment is not a draft shipment"`), proving the earlier
`DiscardShipment` had already fully done its job.

**Two real bugs found during this pass, both confirmed by the user for fixing:**

- [x] G. **`_snake_case()` acronym-splitting bug** (`tools/commands.py`): the naive
      `re.sub(r"(?<!^)(?=[A-Z])", "_", name)` split every capital letter individually, so
      `GenerateShipmentPDF` produced the tool name `..._generate_shipment_p_d_f` instead of
      `..._generate_shipment_pdf` (same for `GenerateUPTimelineExcelReport`) — discovered when a
      tool call by the expected name came back "Unknown tool" during the real-data PDF-generation
      step. Fixed with a two-pass regex that treats a run of capitals as one acronym (splitting
      only before a capital that starts a new lowercase word, and between a lowercase/digit and a
      following capital), verified against all 59 command operation names with no regressions or
      new name collisions.
- [x] H. **Incorrect "`All` is a wildcard" claim** for `clm_list_material_handovers`'s `status`
      filter: the original docstring (written before real data existed to test against) guessed
      `"All"` was a safe default. Real-data testing disproved this live — `"All"` returns
      `TotalCount: 0` while `"InProgress"` returns `TotalCount: 35` on a site with 35 real matching
      handovers, proving `status` is a literal string match, not a wildcard. Corrected the
      docstring in `tools/handovers.py`, plus the same repeated claim in `README.md`'s "Known
      limitations" and `USAGE.md`'s "Operating guidance for coding agents" sections.

**Known, documented, unresolved (environment/API limitations, not code bugs)**: a material
handover created during this pass has no delete/remove operation anywhere in the 59 commands and
remains on the test site, attached to a since-discarded shipment; `LeanCardQuery/GetWorkingPackages`
(and its `Count` variant) and `TimelineQuery/GetShipmentTimelineById` for a nonexistent id return
upstream 500s regardless of input, confirmed via raw `curl` to not be artifacts of this client.


## Multi-Service Expansion (construction, team, konshub)

### Context

`clm-mcp` today is a production-grade MCP server wrapping exactly one CLM service:
**ClmShipmentWebService** (121 usable operations). It is complete, tested (481 tests passing),
and shipped at `github.com/sabadia/clm-mcp`.

The user now wants the same treatment for three sibling CLM services:

| Service | Spec | Ops (usable) | Query | Command |
|---|---|---:|---:|---:|
| shipment *(existing)* | `business-clm-shipment` | 121 | 62 | 59 |
| **construction** | `business-clm-construction` | 96 | 44 | 52 |
| **team** | `business-clm-team` | 61 | 30 | 31 |
| **konshub** | `business-clm-konshub` | 86 | 47 | 39 |
| **TOTAL** | | **364** | **183** | **181** |

The goal is one MCP server that covers all four, without the tool surface becoming unusable.

#### Why this needs a plan rather than "repeat what we did for shipment"

Measured, not estimated (see *Verified facts* below): repeating the current design across four
services produces **~204 tools and ~85,000 tokens of tool definitions transmitted on every
request**. That is a non-starter — it would consume a large fraction of the context window before
the user has said anything. The current shipment-only server already costs 82 tools / ~34K tokens,
which is itself a drift from the original plan's stated "~6 KB of tool-definition context" goal.

So the central design decision here is **how to expose 181 write operations without paying 78K
tokens for them**, and the answer (approved below) is to make `clm_invoke` the default write path
and the per-operation generated write tools opt-in per service.

---

### Verified facts (measured live during planning — do not re-derive)

#### Structural uniformity — the good news

All four specs are the same shape, which is what makes a single merged server tractable:

| Property | All four services |
|---|---|
| OpenAPI version | 3.0.1 |
| Path shape | `/{Service}WebService/{Tag}/{Operation}` — **exactly 3 segments, always** |
| Templated path params (`{id}`) | **zero**, in all four |
| `operationId` | absent (1 stray in construction) — names must stay path-derived |
| `securitySchemes` | absent in all four — Bearer is inferred, as before |
| Tag convention | `*Query` / `*Command` / `Test` holds in **all four** |
| `summary` quality | high, present on 93–99% of ops — reuse verbatim |
| `servers[0].url` | `http://msblocks.selisestage.com/` — **wrong in all four** (plaintext, no gateway prefix). Keep ignoring it. |
| Query envelope | `{Data, IsSuccess, StatusCode, ErrorMessage, PropertyName, ValidationErrors, TotalCount}` — **byte-identical across all four** |
| Command envelope | `{RequestUri, ExternalError, HttpStatusCode, Errors, ErrorMessages, StatusCode}` — **byte-identical across all four** |

**Consequence: `api/envelope.py`, `api/errors.py`, `spec/shaping.py` and all of `auth/` need
zero changes.** One identity issuer serves all four services, so one `TokenManager` and one
`httpx.AsyncClient` remain correct.

Base URLs follow `https://msblocks.selisestage.com/api/business-clm-{slug}`, and the spec `path`
already carries the `/Clm{Service}WebService/` segment — so `base_url_for(service) + operation.path`
is the complete routing rule.

#### Name-collision analysis (measured)

- Merging all four specs into one registry keyed by the current `{Tag}/{PathTail}` scheme collides
  on exactly **two** keys: `Test/GetUserData` (all 4) and `Test/TestHost` (construction + team) —
  **both inside the already-excluded `Test` tag**. Among the 364 usable operations there are
  **zero collisions**.
- 25 operation *names* repeat across services (`GetShipmentComments`, `GetVendorTeams`,
  `GetMaterialHandover`, …) but always under different tags, so the tag prefix disambiguates them.
- Generated write-tool names under today's `clm_{tag}_{op}` scheme: **181 names, 181 unique, 0
  collisions**. Adding a service segment would push 27 names over 64 chars (vs 8 today), so the
  service segment is *not* worth adding.

#### 🔴 The tool-surface explosion (the reason for this plan)

| Measurement | Value |
|---|---|
| Today, shipment-only, writes on | **82 tools, 135,298 bytes ≈ 33,824 tokens** |
| — of which 59 generated write tools | 106,494 bytes ≈ 26,623 tokens (avg 1,804 B each) |
| Projected 181 write tools, 4 services | **313,068 bytes ≈ 78,267 tokens** |
| Projected naive 4-service total | **~204 tools, ~85,000 tokens** |

Per-service projected write-tool cost: shipment 32.0K tok, konshub 27.5K, construction 13.1K,
team 5.6K. konshub is disproportionately expensive because `KonsHubShipment` has 118 properties.

#### 🔴 Cyclic schemas — a hard blocker in the current registry

`spec/registry.py` inlines `$ref`s eagerly and **raises `SchemaCycleError` on any cycle**. The
shipment spec is acyclic, so this has never fired. The new specs are not:

- **konshub**: `KonsHubShipment.PreviousKonsHubShipments` → `KonsHubShipment` (direct self-reference)
- **construction**: `Reservation` ↔ `ReservationObject`, and `ReservationObject` ↔
  `ReservationObjectSpecificDate`

Blast radius is 4 operations, but one is a **write request body**, which feeds the synthesized
Pydantic params model:

| Service | Operation | Where |
|---|---|---|
| konshub | `KonsHubShipmentCommand/SplitKonsHubShipment` | **request** |
| konshub | `KonsHubShipmentQuery/GetDeliveriesTabShipmentList` | response |
| konshub | `KonsHubShipmentQuery/GetShipmentDetailsForMobile` | response |
| construction | `ConstructionManagementQuery/GetSiteOverviewAndUPReservation` | response |

Without a fix the registry fails at import and **the server does not start at all**.

#### 🔴 `Test/*` is a security exclusion in all four services

All four expose `Test/GetUserData` (the endpoint confirmed live on shipment to return
`SuperAdminUserEmail` / `SuperAdminUserCredential` in plaintext to any authenticated caller).
`team` additionally exposes `ProcessTeamsDataMigration` / `ProcessTeamsDataMigrationInWebService`
and `TestNotificationService`; construction and konshub expose `Process*DataInBackground`.

19 `Test/*` operations total. The existing **tag-level** exclusion in `registry._build` covers
every one of them automatically — no new exclusion logic is needed, and this must be verified by
test per service. The upstream credential-leak defect remains a *reporting* obligation, not
something this client fixes.

#### Other measured facts

- `SiteId`-style scoping applies to 36–47% of operations in every service, so the existing
  JWT-`site_id` defaulting pattern carries over. Construction has casing drift worth handling:
  `SiteId`, `siteId`, **`Siteid`** (×2), `SiteIds`, `ProjectSiteId`.
- Named-but-valueless int enums exist in all four (konshub: `ApprovalStatus`, `CommissionedStatus`,
  `StackableType`, `ShipmentDetailType`; team: `TeamType`, `ClmTeamType`, `RequestType`;
  construction: `LocationType`, `WKTType`, `OSType`, `RepeatType`, `SeverityLevel`). Same
  "UNKNOWN_n placeholder" treatment as `ShipmentStatus`.
- Biggest DTOs: konshub `KonsHubShipment` (118 props), construction `ClmTeam` (43),
  team `ExtendedPersonResponseModel` (42). Response shaping stays load-bearing.

---

### Approved design decisions

Confirmed with the user during planning:

1. **Gateway-first writes, opt-in generated write tools.** All four services always load. Writes go
   through `clm_invoke` by default; per-operation write tools are opt-in per service via a new
   `CLM_WRITE_TOOLS` setting. *This changes today's shipment default* (59 write tools → 0) and must
   be called out prominently in the README and in the release notes.
2. **~6–8 curated read tools per new service** (~24 new), covering the highest-value workflows.
   Everything else stays reachable through the gateway.
3. **Break schema cycles with a placeholder + depth cap**, rather than excluding the 4 affected
   operations or switching to `$defs`.
4. **Full verification sweep + real-data E2E** against staging across all four services.

---

### Target tool surface

| Group | Count | Notes |
|---|---:|---|
| meta | 2 | `clm_whoami`, `clm_list_enums` |
| gateway | 3 | `clm_list_operations`, `clm_describe_operation`, `clm_invoke` — now cover all 364 ops |
| curated reads — shipment | 20 | unchanged (pre-existing, across 6 tool modules) |
| curated reads — construction | 8 | new |
| curated reads — team | 8 | new |
| curated reads — konshub | 8 | new |
| generated write tools | **0 by default** | opt-in per service |
| **Default total** | **~49 tools, ~15K tokens** | vs ~204 tools / ~85K naive |

Opt-in examples: `CLM_WRITE_TOOLS=shipment` → ~108 tools; `CLM_WRITE_TOOLS=all` → ~230 tools
(supported, loudly documented as expensive).

**As-built correction** (found during the post-implementation gap review): shipment's curated
count is **18**, not 20 (`shipments.py` 8 + `incidents.py` 2 + `equipment.py` 2 +
`lean_cards.py` 2 + `handovers.py` 2 + `cockpit.py` 2 = 18) — the "20" above was a planning
estimate never reconciled against the actual pre-existing module count. Measured default
total is **47 tools / ~66,000 bytes (~16,500 tokens)**, not ~49/~15K. `README.md`/`USAGE.md`
were corrected to the measured numbers (42 curated + 2 meta + 3 gateway = 47; 322 operations
— 181 writes + 141 remaining queries — reachable only via the gateway).

---

### Design

#### Service identity — one new module, `src/clm_mcp/services_catalog.py`

The single source of truth for "what is a CLM service". Deliberately **not** in `config.py`
(avoids a circular import with `spec/registry.py`) and **not** in `services/` (that package is the
business-composition layer; a name clash there would be confusing).

```python
@dataclass(frozen=True, slots=True)
class ClmService:
    slug: str  # "shipment" | "construction" | "team" | "konshub"
    spec_filename: str  # "shipment.json"
    gateway_segment: str  # "business-clm-shipment"
    path_prefix: str  # "/ClmShipmentWebService"  (assert every op path starts with this)
    title: str  # "ClmShipmentWebService"


SERVICES: Final[Mapping[str, ClmService]] = {...}  # 4 entries, insertion-ordered
DEFAULT_SERVICE: Final = "shipment"
```

`path_prefix` is not decorative: assert at registry build time that every operation's path starts
with its service's prefix, so a future spec refresh that changes the gateway shape fails loudly
instead of silently producing 404s.

#### Operation naming — canonical qualified, backwards-compatible unqualified

Canonical registry key becomes **`{slug}/{Tag}/{PathTail}`**:

```
shipment/ShipmentQuery/GetShipmentById
konshub/KonsHubShipmentCommand/SplitKonsHubShipment
```

Because the collision analysis proved the unqualified `{Tag}/{PathTail}` form is unique across all
364 usable operations, `clm_invoke` / `clm_describe_operation` **must keep accepting the
unqualified form** so every existing README/USAGE example and any saved agent prompt keeps working:

`OperationRegistry.resolve(name)` →
1. exact match on the canonical key;
2. else unique match on the unqualified `{Tag}/{PathTail}` suffix → return it;
3. else if several match → `ToolError` listing the qualified candidates;
4. else → the existing "Unknown operation" `ToolError`.

Add a test pinning that the unqualified form still resolves for a representative shipment
operation, and that an artificially ambiguous name produces the disambiguating error.

#### Cycle-safe `$ref` resolution — `spec/registry.py`

Replace the `SchemaCycleError` raise in `_resolve_ref` with cycle-breaking:

- On re-entering a schema name already on the **current resolution path**, emit
  `{"type": "object", "title": name, "description": f"<recursive reference to {name}; use clm_describe_operation for its shape>"}`
  instead of recursing.
- Add an absolute depth cap (`_MAX_RESOLUTION_DEPTH = 40`) emitting a similar
  `<schema nesting depth-capped>` placeholder, as a belt-and-braces guard against a pathological
  future spec.
- Keep `SchemaCycleError` as a public symbol — `scripts/refresh_spec.py` imports it — but it
  becomes unused by the resolver. Either keep it exported for compatibility or remove it from the
  script in the same change; do not leave a dangling import.
- The placeholder must be valid JSON Schema, because `clm_invoke` feeds `request_schema` straight
  into `jsonschema.validate` and `commands.py` feeds it into `create_model`. `{"type": "object"}`
  validates any object, which is the correct permissive behavior at a truncation point.

Tests: pin the placeholder shape for `KonsHubShipment` self-reference and the construction
`Reservation` mutual cycle; assert all four affected operations are describable and invocable.

#### Multi-spec registry

- Vendor specs to `src/clm_mcp/spec/specs/{slug}.json` (four files; move the existing
  `swagger.json` → `specs/shipment.json`). `pyproject.toml` already ships the package by directory,
  so no packaging change is needed — but verify the built wheel contains all four.
- `Operation` gains a `service: str` field. `OperationSummary` gains `service` too, so
  `clm_list_operations` output tells the model which service an operation belongs to.
- `OperationRegistry.__init__` takes `specs: Mapping[str, dict]` (slug → parsed spec) instead of a
  single spec, and builds the merged index. Duplicate canonical keys still raise `ValueError`.
- `get_registry()` **stays a zero-argument `@lru_cache(maxsize=1)` singleton loading all four
  specs.** This is deliberate and it is what makes the refactor small: because the approved design
  always loads all four services, the registry never needs to depend on `Settings`, so the
  import-time `_REGISTRY = get_registry()` bindings in `tools/gateway.py`, `tools/commands.py` and
  `services/common.py` keep working untouched.
- `list_operations()` gains a `service: str | None` filter, and `tags()` a `service` filter.

#### Per-service base URLs — `config.py` + `api/client.py`

Add to `Settings`:

```python
api_root_url: str = "https://msblocks.selisestage.com/api"
api_base_url: str | None = None  # DEPRECATED alias -> overrides the shipment base URL
api_base_url_overrides: dict[str, str] = {}  # CLM_API_BASE_URL_OVERRIDES, slug -> full URL
write_tools: str = ""  # CLM_WRITE_TOOLS: "", "all", or "shipment,konshub"


def base_url_for(self, slug: str) -> str: ...
def write_tool_services(self) -> frozenset[str]: ...  # parses + validates slugs
```

`base_url_for` precedence: per-service override → `api_base_url` when `slug == "shipment"` →
`f"{api_root_url}/{SERVICES[slug].gateway_segment}"`.

Keeping `api_base_url` as a shipment-scoped override is what preserves every existing `.env`,
every client config, and the ~6 test modules that construct `Settings(api_base_url=...)`.

`api/client.py::_send_once` changes on exactly one line:

```python
url = f"{self._settings.base_url_for(operation.service)}{operation.path}"
```

`AppContext` is **unchanged** — routing is per-operation, so one `ClmApiClient` still serves all
four services. This avoids touching the ~20 `app.api_client` call sites.

`write_tool_services()` must reject unknown slugs with a clear error naming the valid ones, and
should be validated at startup (in `__main__.py`) rather than silently at tool-registration time.

#### Write-tool gating — `tools/commands.py`

Two orthogonal settings, documented as such:

| Setting | Default | Controls |
|---|---|---|
| `CLM_ENABLE_WRITES` | `true` | whether write operations may **execute at all** (gates `clm_invoke` *and* generated tools) |
| `CLM_WRITE_TOOLS` | `""` (none) | which services get **generated per-operation write tools** |

`register()` becomes:

```python
def register(mcp, settings) -> None:
    if not settings.enable_writes:
        return
    enabled = settings.write_tool_services()
    if not enabled:
        return
    for op_summary in _REGISTRY.list_operations():
        operation = _REGISTRY.get(op_summary.name)
        if operation is None or not operation.is_command:
            continue
        if operation.service not in enabled:
            continue
        ...
```

Also in this module:
- Add a **hard uniqueness assertion** on generated tool names (raise on duplicate rather than
  letting `add_tool` silently overwrite), since the guarantee is now measured across four specs.
- Add `_WORD_OVERRIDES = {"KonsHub": "Konshub"}` applied before `_snake_case`, so konshub tools
  read `clm_konshub_shipment_command_…` rather than `clm_kons_hub_shipment_command_…`. Extend the
  existing `_snake_case` parametrized regression test with konshub/construction/team cases.
- `_build_params_model`'s model name uses the canonical key with `/` → `_`, which now includes the
  service slug — that keeps synthesized model names unique across services for free.

#### Curated read tools — 24 new

Three new tool modules + three new service modules, following the existing pattern exactly (thin
tool functions; `resolve_site_id`/`call_list`/`call_object` from `services/common.py`; module-local
`_FieldsParam` / `_SiteIdParam`; `ToolAnnotations(read_only_hint=True, idempotent_hint=True)`).

**`tools/construction.py` + `services/construction.py`**
| Tool | Operation |
|---|---|
| `clm_list_materials` | `ConstructionManagementQuery/GetMaterialList` |
| `clm_get_material` | `ConstructionManagementQuery/GetMaterialById` |
| `clm_get_material_usage` | `ConstructionManagementQuery/GetMaterialUsagesById` |
| `clm_list_zones` | `ZoneQuery/GetZones` *(GET)* |
| `clm_get_zone` | `ConstructionManagementQuery/GetZoneById` *(GET)* |
| `clm_list_site_locations` | `ConstructionManagementQuery/GetSiteLocations` *(GET)* |
| `clm_get_site_structure` | `ConstructionManagementQuery/GetSiteStructuresBySite` |
| `clm_search_wiki` | `WikiQuery/SearchWikiContentByKeyword` |

**`tools/team.py` + `services/team.py`**
| Tool | Operation |
|---|---|
| `clm_list_teams` | `ClmTeamQuery/GetTeamList` |
| `clm_list_team_members` | `ClmTeamQuery/GetTeamMembersForTeam` |
| `clm_list_vendor_teams` | `ClmTeamQuery/GetVendorTeams` |
| `clm_list_join_requests` | `ClmTeamQuery/GetJoinRequestList` |
| `clm_get_join_request` | `ClmTeamQuery/GetJoinRequestDetails` |
| `clm_list_site_responsible_persons` | `ClmTeamQuery/GetSiteResponsiblePersons` |
| `clm_get_person_info` | `ClmTeamQuery/GetExtendPersonInfo` |
| `clm_list_invitations` | `ClmTeamQuery/GetInvitationList` |

**`tools/konshub.py` + `services/konshub.py`**
| Tool | Operation |
|---|---|
| `clm_list_konshub_incoming_shipments` | `KonsHubShipmentQuery/GetDashboardIncommingShipmentList` |
| `clm_list_konshub_outgoing_shipments` | `KonsHubShipmentQuery/GetDashboardOutgoingShipmentList` |
| `clm_list_konshub_deliveries` | `KonsHubShipmentQuery/GetDeliveriesTabShipmentList` |
| `clm_count_konshub_deliveries` | `KonsHubShipmentQuery/GetDeliveriesTabShipmentCount` |
| `clm_search_konshub_deliveries` | `KonsHubShipmentQuery/SearchDeliveriesShipment` |
| `clm_get_konshub_shipment_comments` | `KonsHubShipmentQuery/GetShipmentComments` |
| `clm_list_storage_zones` | `StorageCommissionQuery/GetStorageZones` |
| `clm_list_warehouse_zones` | `WareHouseQuery/GetKonsHubZones` |

Naming rule applied above: konshub shipment-ish tools carry a `konshub_` infix because the
shipment service already owns the unprefixed names. Verify no curated name collides with an
existing one *or* with a generated write-tool name.

Use the canonical qualified operation names (`"konshub/KonsHubShipmentQuery/…"`) in the service
layer, so curated tools don't depend on the unqualified-alias fallback.

#### Remaining touch points

- **`enums.py`** — add placeholder `IntEnum`s for the newly discovered named int fields and group
  `DOMAIN_ENUMS` by service; `EnumInfo` gains a `service` field. Keep the "don't guess names"
  `UNKNOWN_n` discipline. Extend `test_enums.py`'s value-pinning accordingly.
- **`server.py`** — rewrite `SERVER_INSTRUCTIONS` to describe all four domains, state that writes
  default to `clm_invoke`, and point at `clm_list_operations(service=...)` for discovery. No
  structural change.
- **`scripts/refresh_spec.py`** — loop over `SERVICES`, fetch each swagger URL, diff per service,
  and write to `specs/{slug}.json`. Add `--service` to refresh one.
- **`tools/gateway.py`** — `clm_list_operations` gains a `service` filter and returns `service` in
  each row; `clm_describe_operation` returns `service`; `clm_invoke`'s write-refusal message should
  mention `CLM_WRITE_TOOLS` is *not* what gates execution (`CLM_ENABLE_WRITES` is), since that will
  otherwise be the top confusion.
- **`__main__.py`** — validate `CLM_WRITE_TOOLS` slugs at startup; surface the enabled services and
  write-tool services in the existing structlog `server.lifespan_started` event.
- **`.env.example`** — document `CLM_API_ROOT_URL`, `CLM_WRITE_TOOLS`, per-service overrides, and
  mark `CLM_API_BASE_URL` deprecated.

---

### Files

**New**
```
src/clm_mcp/services_catalog.py
src/clm_mcp/spec/specs/{shipment,construction,team,konshub}.json
src/clm_mcp/tools/{construction,team,konshub}.py
src/clm_mcp/services/{construction,team,konshub}.py
tests/test_services_catalog.py
tests/test_registry_multiservice.py
tests/test_tools_construction.py
tests/test_tools_team.py
tests/test_tools_konshub.py
tests/test_write_tool_gating.py
```

**Modified**
```
src/clm_mcp/spec/registry.py     # multi-spec merge, service field, cycle-breaking resolver, resolve()
src/clm_mcp/config.py            # api_root_url, base_url_for, write_tools, overrides
src/clm_mcp/api/client.py        # one line: base_url_for(operation.service)
src/clm_mcp/tools/commands.py    # per-service gating, _WORD_OVERRIDES, uniqueness assertion
src/clm_mcp/tools/gateway.py     # service filter + service in output
src/clm_mcp/tools/__init__.py    # register the 3 new modules
src/clm_mcp/tools/meta.py        # EnumInfo.service
src/clm_mcp/enums.py             # per-service enum placeholders
src/clm_mcp/server.py            # SERVER_INSTRUCTIONS
src/clm_mcp/__main__.py          # validate CLM_WRITE_TOOLS
scripts/refresh_spec.py          # 4 services
tests/test_full_coverage.py      # pinned counts 121/62/59 -> 364/183/181, parametrize per service
tests/test_integration.py        # `len(names) > 80` assertion no longer holds by default
tests/test_registry.py           # cycle test now asserts breaking, not raising
tests/test_tools_commands.py     # default is now zero write tools
pyproject.toml                   # description
README.md  USAGE.md  PLAN.md  .env.example
```

**Untouched (verified service-agnostic):** `api/envelope.py`, `api/errors.py`, `spec/shaping.py`,
all of `auth/`, `logging.py`, `services/common.py`, `server.py::AppContext`.

---

### Task checklist

- [x] 1. `services_catalog.py` with the 4 `ClmService` entries + `tests/test_services_catalog.py`
      (slug/URL/prefix correctness).
- [x] 2. Vendor the 4 specs to `spec/specs/{slug}.json` (move shipment's); update
      `scripts/refresh_spec.py` to loop over `SERVICES` with a `--service` flag.
- [x] 3. `registry.py`: cycle-breaking `_resolve_ref` (placeholder + depth cap). Tests pinning the
      placeholder for konshub's self-reference and construction's mutual cycle.
- [x] 4. `registry.py`: multi-spec merge — `Operation.service`, canonical `{slug}/{Tag}/{Op}` keys,
      `path_prefix` assertion, `service` filters, and `resolve()` with the unqualified-alias
      fallback + ambiguity error. Tests incl. backwards-compat resolution.
- [x] 5. `config.py`: `api_root_url`, `api_base_url_overrides`, `base_url_for`, deprecated
      `api_base_url`, `write_tools` + `write_tool_services()`. Tests for precedence and bad slugs.
- [x] 6. `api/client.py`: route via `base_url_for(operation.service)`; `respx` test asserting a
      konshub op hits the konshub gateway and a shipment op still hits the shipment gateway.
- [x] 7. `tools/commands.py`: per-service gating, `_WORD_OVERRIDES`, name-uniqueness assertion.
      `tests/test_write_tool_gating.py` covering default-none, one service, `all`, and
      `CLM_ENABLE_WRITES=false` beating `CLM_WRITE_TOOLS`.
- [x] 8. `tools/gateway.py`: `service` filter/field; verify all 364 ops describable and all 19
      `Test/*` ops unreachable in every service.
- [x] 9. `enums.py` + `tools/meta.py`: per-service enum placeholders, `EnumInfo.service`. **Scope
      note**: no placeholder `IntEnum`s were added for the 12 newly-discovered fields (konshub's
      `ApprovalStatus`/etc., team's `TeamType`/etc., construction's `LocationType`/etc.) — unlike
      `ShipmentStatus`, none of them have any live-observed cardinality evidence, and inventing a
      guessed range would violate the file's own "don't guess" policy. `DOMAIN_ENUM_SERVICES` +
      `EnumInfo.service` are in place so real ones slot in later with zero structural change; see
      the updated "Open items" below.
- [x] 10. Curated construction tools + service module + tests. **Deviation from the plan's table**:
      `clm_list_zones`/`clm_get_zone` map to `ConstructionManagementQuery/GetZones`+`GetZoneById`
      (not `ZoneQuery` as originally listed) — verified directly against the spec; `ZoneQuery` has
      no `GetZones` operation (it has `GetZonesByEntryPoint` instead, left to the gateway).
- [x] 11. Curated team tools + service module + tests.
- [x] 12. Curated konshub tools + service module + tests.
- [x] 13. `server.py` instructions, `__main__.py` startup validation, `.env.example`.
- [x] 14. Update pinned-count tests (`test_full_coverage.py`, `test_integration.py`,
      `test_tools_commands.py`) and add a **tool-surface budget test** asserting the default
      catalog stays under an explicit tool-count/byte ceiling — this is the regression guard for
      the whole point of this plan. (Pinned-count updates landed as collateral of task 4's registry
      rewrite and task 7's write-tool gating; `tests/test_tool_surface_budget.py` added here.)
- [x] 15. `README.md` / `USAGE.md`: four-service tool reference, the `CLM_WRITE_TOOLS` opt-in and
      **a prominent note that shipment write tools are no longer on by default**, per-service base
      URL config, and the `Test/*` security note extended to all four services.
- [x] 16. Copy this plan to repo-local `PLAN.md` (per global CLAUDE.md) and mark items as completed.
- [x] 17. Full sweep: `ruff format`, `ruff check`, `mypy --strict`, `pytest -q`. Fix all findings.
- [x] 18. Real-data E2E against staging across all four services — user supplied credentials
      (`aardo@yopmail.com`). See "Post-implementation gap review + real-data E2E pass" below for
      the full findings: 2 real client-side bugs found and fixed (pagination default, cockpit
      date-range cap), 7 confirmed genuine upstream bugs (documented, not fixable client-side),
      plus 5 smaller doc/naming gaps found and fixed. Write path confirmed working end-to-end for
      all three new services via `clm_invoke`, with zero created side effects (the one
      low-risk/reversible create+delete candidate identified failed upstream validation before
      creating anything). 1274 tests passing (up from 1269), full sweep clean.

---

### Verification

**Automated gate (must all pass before any completion claim):**
```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src/
uv run pytest -q
```

**Tool-surface budget** — the regression guard for this plan's central goal:
```bash
uv run python -c "..."   # build_server + register_all, list_tools, sum tool-def bytes
# default:                 assert ~49 tools, < 20K tokens
# CLM_WRITE_TOOLS=shipment: assert +59 tools
# CLM_WRITE_TOOLS=all:      assert +181 tools, 0 duplicate names
```

**Startup smoke** — the registry now loads 4 specs at import; confirm no `SchemaCycleError`:
```bash
uv run python -c "from clm_mcp.spec.registry import get_registry; r=get_registry(); print(len(r))"
# expect 364
```

**Live E2E (requires `uv run clm-mcp login` — no stored credentials found during planning):**
1. `clm_whoami` → confirm identity, site, roles.
2. One curated read per service against the caller's real site; record which services actually have
   data (shipment's site previously had zero reference data, so expect empty `Data` on some).
3. `clm_invoke` a `*Query` op in each of the four services → confirms base-URL routing, auth reuse
   across gateways, and envelope handling per service.
4. `clm_describe_operation` on all four cycle-affected operations → confirm the placeholder renders
   and the schema is JSON-serializable.
5. `clm_invoke("konshub/KonsHubShipmentQuery/GetDeliveriesTabShipmentList", {...})` → the one that
   exercises a cycle-broken **response** path end-to-end.
6. Confirm `clm_invoke` on a `Test/*` operation in each service is **refused**.
7. Writes: exercise at most one reversible command (or none) — do not run
   `ProcessTeamsDataMigration` or any `Process*Data*` operation against staging.

**MCP Inspector** — `npx @modelcontextprotocol/inspector uv run --project . clm-mcp`; confirm the
default catalog is ~49 tools and `clm_list_operations(service="team")` returns 30 query ops.

---

### Open items / risks

- **Behavior change:** shipment write tools go from on-by-default to opt-in. This is the single
  most user-visible consequence and needs prominent README + release-note coverage. Anyone relying
  on `clm_shipment_command_*` must set `CLM_WRITE_TOOLS=shipment`.
- **Cycle placeholders lose type information.** `SplitKonsHubShipment`'s
  `PreviousKonsHubShipments` becomes an untyped object array, so `clm_invoke` will not catch a
  malformed nested shipment before the HTTP call. Acceptable (truncation is permissive by design),
  but it means that one operation gets weaker pre-flight validation than the other 363.
- **Unverified against real data.** Curated tool parameter choices for the 3 new services are
  derived from spec summaries only. Expect to revise field projections and required-vs-optional
  params after step 18 — exactly as the `status`-is-not-a-wildcard bug surfaced for shipment.
- **Construction `SiteId` casing drift** (`Siteid`, `siteId`) means blind `SiteId` injection will
  silently fail on some construction operations. Each curated construction tool must use the
  casing its own operation declares; do not assume.
- **Upstream `Test/GetUserData` credential leak now confirmed present in all four services.** Still
  needs reporting and rotation upstream; excluding it here does not fix the exposure.
- **Enum names remain unknown** across all four services. Filtering by status stays guesswork until
  the real labels are supplied from the CLM source.
- **konshub tool-definition cost is disproportionate** (27.5K tokens for 39 write tools, driven by
  the 118-property `KonsHubShipment`). If `CLM_WRITE_TOOLS=all` is ever used routinely, konshub is
  the first candidate for a trimmed params model.

---

### Post-implementation gap review + real-data E2E pass (user-directed)

User request: "revise the full plan and implementation and find out if there is any gap at all
or not. if there is any then fill those gaps and implement them. finally do a end to end test
with real data" — credentials supplied (`aardo@yopmail.com`, staging). Logged in via
`clm-mcp login`; same account/site as the original shipment-only E2E pass
(`68BC0C11-963F-46CB-AF93-267B50ABCCAF`, "Scclm Team", Super Admin).

#### Gaps found and fixed (code/docs, before any live testing)

- [x] **Stale single-service docstrings**: `src/clm_mcp/__init__.py` and `__main__.py`'s Typer
      `help=` still said "the SELISE CLM Shipment REST API" — updated to name all four services.
- [x] **`pyproject.toml`'s `description`** — same stale wording, updated.
- [x] **`WareHouse` word-split gap** in `tools/commands.py::_WORD_OVERRIDES` — same category as
      the already-known `KonsHub` fix: `WareHouseCommand`/`CreateWareHouse`/etc. were splitting
      into `ware_house_command`/`create_ware_house` instead of reading as one word. Added
      `"WareHouse": "Warehouse"`; extended the `_snake_case` regression test.
- [x] **Miscounted tool totals in `README.md`/`USAGE.md`**: the intro bullet's "23 covering
      shipment... plus 8 each for construction/team/konshub" implied 23 shipment-domain curated
      tools when the real count is 18 (the "23" was accidentally counting meta+gateway tools
      folded into the shipment figure) — the sum (47) was coincidentally right, but the
      breakdown was wrong. `USAGE.md` said "~317 remaining operations"; actual is 322 (181
      writes + 141 remaining queries). Both corrected to measured values: 2 meta + 42 curated
      (18 shipment + 8 + 8 + 8) + 3 gateway = 47; 322 operations reachable only via the gateway.
- [x] **`PLAN.md`'s own "Target tool surface" table** had the same shipment-count error (20 vs
      actual 18) and total (~49 vs actual 47) — added an "as-built correction" note rather than
      silently rewriting the historical planning estimate.

#### Real bugs found live, fixed

- [x] **1-indexed vs 0-indexed pagination mismatch (team + konshub)**: `services/team.py`'s
      and `services/konshub.py`'s curated list tools defaulted `page_number=0` (matching
      shipment/construction's convention), but `ClmTeamQuery/GetJoinRequestList` and
      `KonsHubShipmentQuery/GetDeliveriesTabShipmentList` (and `GetTeamList`) compute
      `skip = (PageNumber - 1) * PageSize` internally — `PageNumber=0` produces `skip=-PageSize`,
      rejected upstream with `500 Value is not greater than or equal to 0: -5. (Parameter
      'skip')`. Confirmed via raw `curl` independent of this client. Fixed: `page_number`
      defaults to `1` (not `0`) for all of `clm_list_teams`, `clm_list_team_members`,
      `clm_list_join_requests`, `clm_list_invitations`, `clm_list_konshub_incoming_shipments`,
      `clm_list_konshub_outgoing_shipments`, `clm_list_konshub_deliveries`,
      `clm_search_konshub_deliveries`; `ge=1` on the Field constraint; docstrings updated to
      state the one-based convention explicitly. Verified live: `clm_list_join_requests`,
      `clm_list_invitations`, and `clm_list_konshub_deliveries` went from a hard 500 to a clean
      `{"data": [], "total_count": 0}`.
- [x] **`clm_get_cockpit_weekly_counts` had no response-size bound**: unlike every
      list-returning tool (`shape_list_response`'s byte cap), this object-shaped tool wraps an
      uncapped per-day array. Verified live: a 2020–2027 date range returned 2,558 daily entries
      totalling **~530,000 bytes** — over 10x `CLM_MAX_RESPONSE_BYTES`'s default, silently. Fixed
      in `services/cockpit.py::get_cockpit_weekly_counts`: raises `ToolError` for a window over
      92 days, before the HTTP call. Verified live: the same wide range is now refused with a
      clear message; a 7-day window still succeeds normally. Added `tests/test_tools_cockpit.py`
      (previously had zero dedicated tests).
- [x] **Test-isolation bug in `test_token_manager.py`** (found as a side effect of logging in
      for this pass): `test_no_credentials_raises_missing_credentials_error` constructed
      `Settings()` without overriding `credentials_path`, so it silently depended on
      `~/.config/clm-mcp/credentials.json` **not existing** on the machine running the test — it
      started failing the moment real credentials were stored there (exactly what `clm-mcp
      login` just did). Fixed to point at a guaranteed-nonexistent path, matching the pattern
      `test_auth_error_propagation.py` already used correctly.

#### Confirmed genuine upstream bugs (verified via raw `curl`, independent of this client — not
fixable client-side; reported here, not remediated)

| Operation | Symptom |
|---|---|
| `construction/ConstructionManagementQuery/GetMaterialList` | `500`, `Object reference not set to an instance of an object.`, regardless of input (even `{}`) |
| `team/ClmTeamQuery/GetTeamList` | `Exception has been thrown by the target of an invocation.` once past pagination validation, with a real, valid `SiteId` |
| `konshub/KonsHubShipmentQuery/GetDashboardIncommingShipmentList` | same generic invocation-target exception, regardless of input |
| `konshub/KonsHubShipmentQuery/GetDashboardOutgoingShipmentList` | same |
| `team/ClmTeamQuery/GetVendorTeams` | `"Provided SiteId does not exists"` for the authenticated admin's own real, valid site — an environment/data-setup quirk (this site isn't registered as vendor-team-scoped), not a malformed request |
| `team/ClmTeamQuery/GetSiteResponsiblePersons` | response self-inconsistency: `TotalCount: 1` but `Data: []` |
| `construction/ZoneCommand/CreateZone` | rejects the same real `SiteId` with `"The site does not exist"` — this site isn't registered as a construction site in this environment |

(Previously known and still present, unrelated to this pass: `shipment/LeanCardQuery/GetWorkingPackages`
and its `Count` variant, `shipment/TimelineQuery/GetShipmentTimelineById` for a nonexistent id.)

#### Write-path verification

Full create → verify → delete lifecycle testing (as done for shipment in the original pass) was
**not repeatable** for the three new services on this environment: the one clean, low-risk,
fully-reversible candidate identified (`construction/ZoneCommand/CreateZone` +
`ZoneCommand/DeleteZone`) failed validation before creating anything — `'Location Type' must not
be equal to '0'`, `'WKT Type' must not be equal to '0'`, and `"The site does not exist"` — and no
enum values for `LocationType`/`WKTType` are known (see enums.py's "don't guess" policy), so no
retry was attempted. Nothing was created or modified.

Instead, the write **path** itself (auth injection, per-service base-URL routing, JSON body
posting, envelope unwrapping, `ToolError` surfacing) was confirmed working end-to-end for all
three new services via `clm_invoke` against real gateways with zero created side effects:
- construction: `ZoneCommand/CreateZone` → clean, well-formed FluentValidation errors returned.
- team: `ClmTeamCommand/CreateContactPersonJoinRequest` (empty params) → clean business error
  (`"Provided 'Email' does not exists"`).
- konshub: `KonsHubShipmentCommand/CreateShipmentComment` (empty params) → upstream NRE, correctly
  surfaced as a `ToolError` (an input-validation gap on the API's side, not this client's).
- `DeleteZone` against a never-created `ItemId` returned a clean success — confirms delete is
  idempotent/soft rather than existence-checked.

#### Other live confirmations (no gap — working as designed)

- `Test/*` refused in all four services, both canonical (`shipment/Test/GetUserData`) and
  unqualified (`Test/GetUserData`) forms.
- All four cycle-affected operations (`SplitKonsHubShipment`,
  `GetDeliveriesTabShipmentList`, `GetShipmentDetailsForMobile`,
  `GetSiteOverviewAndUPReservation`) describable, JSON-serializable, and (for
  `GetDeliveriesTabShipmentList`) invocable end-to-end with a real response.
- `scripts/refresh_spec.py` run live against all four real swagger endpoints: zero drift.
- Per-service base-URL routing confirmed live for all four gateways (each request logged/observed
  hitting its own `business-clm-{slug}` host).
- Clean, correct, null-stripped/shaped responses confirmed live for: `clm_search_shipments`,
  `clm_list_shipments`, `clm_count_shipments`, `clm_list_equipment`, `clm_list_zones`,
  `clm_list_site_locations`, `clm_get_site_structure`, `clm_search_wiki`,
  `clm_list_join_requests` (post-fix), `clm_list_invitations` (post-fix),
  `clm_list_site_responsible_persons` (modulo the TotalCount/Data inconsistency above),
  `clm_list_konshub_deliveries` (post-fix), `clm_count_konshub_deliveries`,
  `clm_search_konshub_deliveries`, `clm_list_storage_zones`, `clm_list_material_handovers`,
  `clm_get_weather` (via the mocked-then-live path).

#### Final verification after this pass

`ruff format --check`, `ruff check`, `mypy --strict src/`, `pytest -q` (1274 passed, up from
1271 — 3 new cockpit tests) all clean. See the final report delivered to the user for the
complete narrative.

---

### Full-tool-catalog E2E pass + major correction (user-directed follow-up)

User asked explicitly whether *every* implemented MCP tool had been E2E-tested, and to do so
with real data and a real scenario if not, plus a final success/failure table. The prior pass
above tested a representative subset, not all 47 default tools, and — critically — everything
was tested against the account's one *default* JWT-scoped site
(`68BC0C11-963F-46CB-AF93-267B50ABCCAF`, "Scclm Team"), which turned out to have corrupted or
incomplete underlying data.

#### The correction

Querying `konshub/KonsHubShipmentQuery/GetSiteSelectorData` revealed this Super Admin account
has access to several other, properly-configured sites, including ones explicitly named as test
sites: **"KonsHub Test Site 1"** (`d02be1bb-1224-491f-a2cd-f243dda36754`, warehouse
`a1c30340-6fad-47d3-a214-6ac792e58723`, `CanCreateShipment: true`). Re-running the *exact same
operations* previously reported as "confirmed genuine upstream bugs, regardless of input" against
this site instead returned clean, correct, real data:

| Operation | On the default site | On "KonsHub Test Site 1" |
|---|---|---|
| `LeanCardQuery/GetWorkingPackagesCount` | 500 NullReference | real counts (`ActiveWorkingPackagesCount: 3006`) |
| `ClmTeamQuery/GetTeamList` | 500 invocation exception | real teams |
| `ClmTeamQuery/GetVendorTeams` | `"Provided SiteId does not exists"` | real vendor teams |
| `KonsHubShipmentQuery/GetDashboardIncommingShipmentList`/`Outgoing` | 500 invocation exception | clean empty result |
| `ConstructionManagementQuery/GetMaterialList` | 500 NullReference regardless of `PageNumber` | **was actually the same 1-indexed-pagination bug already found for team/konshub** — masked on the empty/corrupted default site by a NullReference that looked page-number-independent |

**This means most of the previous pass's "confirmed genuine upstream bugs, not fixable
client-side" were misdiagnosed** — they were real failures, reproduced identically via `curl`,
but attributable to one specific site's bad data, not universal API defects. `README.md`,
`USAGE.md`, and this plan have been corrected accordingly. Corrected in-place, not silently:
this is exactly the kind of thing the Corrections policy calls for stating plainly and moving on.

**One more real client-side bug found from this**: `services/construction.py`'s
`list_materials` had the same `page_number=0` default bug as team/konshub, just harder to spot
because the empty default site produced an unrelated-looking NullReferenceException at any
`PageNumber` value. Fixed: default is now `1`, `ge=1`, docstring updated; regression test added.

#### What's still genuinely a defect (reconfirmed on the healthy site, not site-specific)

- `TimelineQuery/GetShipmentTimelineById` still 500s for a **genuinely nonexistent shipment id**,
  independent of site — this one was never a site-data issue, it's a real "should 404" defect.
- `ClmTeamQuery/GetSiteResponsiblePersons`'s `TotalCount` does not track its `Data` array length
  on *either* site (default site: `TotalCount:1`/0 real rows; healthy site: `TotalCount:1`/7 real
  rows) — a genuine, reproducible envelope inconsistency.
- **New finding**: `ConstructionManagementQuery/GetMaterialUsagesById` can return `IsSuccess:
  false` with a fully populated, correct `Data` object and no error message — confirmed via
  `curl` with a real material id. This client's envelope handling correctly (per every other
  operation's contract) treats `IsSuccess: false` as failure and raises `ToolError`, discarding
  good data — there's no client-side fix that wouldn't mean guessing which operations violate
  their own contract.

#### Real-data lifecycle built for this pass (on "KonsHub Test Site 1")

Created via `clm_invoke`, verified by re-querying, then cleaned up: one real shipment
(`ShipmentCommand/UpsertAdhocShipment`, using a real unloading zone `Id` — note the zone's real
identifier field is `Id`, not `ItemId`, discovered by inspecting the raw `GetZones` response), one
comment (`CreateShipmentComment`), one incident (`IncidentCommand/CreateIncident`), one material
handover (`MaterialHandoverCommand/CreateMaterialHandover`) — then `ShipmentCommand/
DiscardShipment`. The incident and handover have no delete operation (same finding as the
original shipment-only pass) and remain attached to the now-discarded shipment on this
explicitly-named test site — an acceptable, low-risk artifact, consistent with that precedent.

#### Full 47-tool result

**46 of 47 succeeded** against real data (see the table delivered to the user). The one failure —
`clm_get_material_usage` — is the `GetMaterialUsagesById` envelope defect above, not a client bug.

#### Final verification after this pass

`ruff format --check`, `ruff check`, `mypy --strict src/`, `pytest -q` (1275 passed, up from
1274 — 1 new construction pagination regression test) all clean.

---

### `GetMaterialUsagesById` fix (user-directed follow-up)

User supplied a working `curl` payload for the one tool that failed in the 47-tool pass
(`clm_get_material_usage`) and noted the endpoint is slow — asked to retest accordingly.

Retested with the user's exact `MaterialId` (`d45f6d25-6686-4176-b757-77e919047689`, on the
account's default site) with a generous timeout: **confirmed via raw `curl` that this operation
always returns `IsSuccess: false`, even on a real, correct lookup** — response took ~9s. A
second real material (from the earlier "KonsHub Test Site 1" data, `528bfff6-...`) reproduced
the same pattern. A genuinely nonexistent `MaterialId` (`00000000-...`) returns `Data: null` in
under 1s, still with `IsSuccess: false` — this is the one reliable failure signal for this
operation. So the bug is not intermittent or timeout-related, and the earlier "no client-side
fix possible" conclusion was too pessimistic: `Data` reliably distinguishes found (populated)
from not-found (`null`), regardless of the always-false flag.

**Fix**: `api/envelope.py` gained a narrow, name-scoped override —
`_ALWAYS_REPORTS_ISSUCCESS_FALSE_OPERATIONS = {"construction/ConstructionManagementQuery/
GetMaterialUsagesById"}`. For exactly that operation, a bare `IsSuccess: false` (no
`ErrorMessage`, no `ErrorMessages`, no `ExternalError`, no failed `ValidationErrors`) with a
non-null `Data` is treated as success instead of raising. Any *other* operation hitting the same
shape still raises, as does this one operation if `Data` is null or a real error is actually
present (both covered by regression tests). `services/construction.py::get_material_usage`'s
docstring documents the quirk and the latency (3–12s observed).

Verified live: both the user's `MaterialId` and the earlier one now return the correct
`{OpenShipmentCount, ApprovedShipmentCount, CompletedShipmentCount, TotalShipmentCount}` object
through `clm_get_material_usage` with no error. `README.md`/`USAGE.md`'s "Known limitations"
updated — this is no longer listed as an unfixable upstream defect, since it's now handled.
**46/47 → 47/47** default tools now succeed against real data.

Tests added: 3 in `test_envelope.py` (the override itself, that a null `Data` still raises, and
that the override doesn't mask a real `ErrorMessage` on the same operation) and 2 in
`test_tools_construction.py` (success path, nonexistent-material path).

Final verification: `ruff format --check`, `ruff check`, `mypy --strict src/`, `pytest -q`
(1281 passed, up from 1275) all clean.

