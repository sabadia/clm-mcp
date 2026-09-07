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
