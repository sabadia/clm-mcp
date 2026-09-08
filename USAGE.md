# clm-mcp — Usage Guide

How to connect `clm-mcp` to every major MCP client, plus operating guidance for a coding
agent (or you) once it's connected. See [`README.md`](README.md) for what the server does,
its full tool reference, and troubleshooting.

All examples below assume the repo lives at `/Users/mahamudul/work/office/clm-mcp` — substitute
your own path. All examples also assume you've already run `uv sync` once in that directory.

---

## 0. One-time setup (do this first, regardless of client)

```bash
cd /Users/mahamudul/work/office/clm-mcp
uv sync
uv run clm-mcp login
#   CLM username (email): you@example.com
#   CLM password: ********          (getpass — never echoed, never sent anywhere but the identity service)
#   Credentials saved to ~/.config/clm-mcp/credentials.json
```

That's it — every client below picks up the stored refresh token automatically (mode `0600`,
readable only by you). None of the client configs need an `env` block unless you'd rather pass
`CLM_REFRESH_TOKEN` (or `CLM_USERNAME`/`CLM_PASSWORD`) inline instead.

The command every client below runs is the same:

```bash
uv run --project /Users/mahamudul/work/office/clm-mcp clm-mcp
```

`--project` tells `uv` where the server's `pyproject.toml`/`uv.lock` live, so the client can be
launched from any working directory.

---

## Claude Code

```bash
claude mcp add clm -- uv run --project /Users/mahamudul/work/office/clm-mcp clm-mcp
```

- `-s project` instead of the default `-s local` writes the entry to `.mcp.json` in a project
  directory, so it's shared with anyone who checks that project out (Claude Code asks them to
  approve it on first use).
- `-s user` makes it available across every project on your machine.
- To pass credentials inline instead of `clm-mcp login`:
  ```bash
  claude mcp add clm -e CLM_REFRESH_TOKEN=<token> -- uv run --project /Users/mahamudul/work/office/clm-mcp clm-mcp
  ```
- Verify: `claude mcp list` (should show `clm`), then `claude mcp get clm` for details.
- Remove: `claude mcp remove clm`.

## Claude Desktop

Edit the config file for your OS (create it if it doesn't exist):

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux (community builds) | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"]
    }
  }
}
```

Fully restart Claude Desktop (quit, not just close the window) after editing. A hammer/tool icon
in the message composer confirms the server connected.

## Cursor

Either project-level (`.cursor/mcp.json` in the repo you're working in — shareable via git) or
global (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"]
    }
  }
}
```

Cursor supports `${env:VAR}` interpolation if you'd rather keep a token out of the file:

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"],
      "env": { "CLM_REFRESH_TOKEN": "${env:CLM_REFRESH_TOKEN}" }
    }
  }
}
```

Reload Cursor (or use **MCP: Reload Servers** from the command palette) after editing.

## Windsurf (Cascade)

Edit `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"]
    }
  }
}
```

Or add it through **Windsurf Settings → Cascade → MCP Servers → Add custom server**, which edits
the same file. Click the refresh icon next to MCP Servers after saving.

## VS Code (Copilot Chat agent mode)

VS Code's native MCP support uses a different top-level key (`servers`, not `mcpServers`) and an
explicit `"type"`. Create `.vscode/mcp.json` in your workspace (or run **MCP: Open User
Configuration** from the command palette for a machine-wide config):

```json
{
  "servers": {
    "clm": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"]
    }
  }
}
```

To avoid a token in plain text, use VS Code's `inputs` prompt (it stores the value securely
after the first run):

```json
{
  "inputs": [
    { "type": "promptString", "id": "clm-refresh-token", "description": "CLM refresh token", "password": true }
  ],
  "servers": {
    "clm": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"],
      "env": { "CLM_REFRESH_TOKEN": "${input:clm-refresh-token}" }
    }
  }
}
```

Start the server from the **MCP: List Servers** command palette entry, or click **Start** on the
inline code-lens VS Code shows above the `"clm"` entry in the JSON file.

## Cline (VS Code extension)

Open Cline's panel → **MCP Servers** tab → **Configure MCP Servers**, which opens its settings
JSON (`cline_mcp_settings.json`) for direct editing:

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Leave `autoApprove` empty at first — Cline will then prompt for approval on every `clm-mcp` tool
call, which is worth doing at least once so you see what each tool actually sends. Add read-only
tool names there later if you want them to run without a prompt (never add a `*_command_*`
write-tool name unless you're comfortable with unattended writes).

## Zed

Zed's key is `context_servers`, in `settings.json` (**zed: open settings file** from the command
palette, or **Settings → AI → MCP Servers → Add Server → Add Local Server** to do it through
the UI):

```json
{
  "context_servers": {
    "clm": {
      "command": "uv",
      "args": ["run", "--project", "/Users/mahamudul/work/office/clm-mcp", "clm-mcp"],
      "env": {}
    }
  }
}
```

## Continue.dev

Continue's `mcpServers` is a **list**, not an object keyed by name — the name is a field inside
each entry. Add to `.continue/config.yaml` (or a standalone file under `.continue/mcpServers/`):

```yaml
mcpServers:
  - name: clm
    type: stdio
    command: uv
    args:
      - run
      - --project
      - /Users/mahamudul/work/office/clm-mcp
      - clm-mcp
```

MCP servers only run in Continue's **agent** mode, not plan/chat mode.

## Any other stdio-based MCP client

If your client isn't listed above but accepts a generic `command`/`args` (and optionally `env`)
stdio server definition — which covers the overwhelming majority of MCP clients, since stdio is
the baseline transport every client supports — use:

```
command: uv
args:    run --project /Users/mahamudul/work/office/clm-mcp clm-mcp
```

## Remote / HTTP clients

For a client that only speaks HTTP (or to run the server once and point multiple clients at it,
e.g. across a team), run it as a long-lived process instead of a per-client subprocess:

```bash
uv run --project /Users/mahamudul/work/office/clm-mcp clm-mcp --http --port 8000
```

Then point any HTTP-capable client at `http://127.0.0.1:8000/mcp` (Cursor's `url` field, VS
Code's `"type": "http"`, Windsurf's `serverUrl`, etc. — see each section above for the stdio
equivalent of the same key names). Note `CLM_ENABLE_WRITES` and credentials are then fixed for
the lifetime of that one process, shared by every client connected to it — there's no per-client
identity. Put it behind a reverse proxy with TLS and access control before exposing it beyond
`127.0.0.1`; the server itself does not implement its own transport-level auth.

---

## Verifying the connection

Regardless of client, ask it (in chat) something like:

> Call clm_whoami

A working connection returns your CLM identity, site, and roles. If it instead reports "No CLM
credentials found," `clm-mcp login` either wasn't run or the client is launching a different
`uv`/Python than the one you ran it with — check the client picked up the same `HOME` (the
credentials file is under `~/.config/clm-mcp/`) and that `args` points at the same repo path you
ran `uv sync`/`clm-mcp login` in.

For a client-agnostic sanity check outside any of the above, the official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) works against any stdio
server without needing a client at all:

```bash
npx @modelcontextprotocol/inspector uv run --project /Users/mahamudul/work/office/clm-mcp clm-mcp
```

---

## Operating guidance for coding agents (and anyone driving this server)

This section is meant to be pasted into a project's `CLAUDE.md`/`AGENTS.md`/system prompt, or
just kept in mind, when a coding agent has this server connected.

- **Call `clm_whoami` before anything else in a session.** It confirms auth is actually working
  and its `site_id` is what every other tool defaults to — you rarely need to pass `site_id`
  yourself.
- **This server wraps four CLM services — shipment, construction, team, konshub — as one tool
  catalog.** They share one login (one 7-minute access token) and one gateway
  (`clm_list_operations`/`clm_describe_operation`/`clm_invoke`), each filterable by
  `service`. `clm_whoami`'s `site_id` applies across all four.
- **Every operation in all four APIs has a way to reach it — nothing is a dead end.** 42
  curated tools (`clm_get_shipment`, `clm_list_teams`, `clm_list_konshub_deliveries`, ...) are
  individually named, typed, and shape their responses to fit context — prefer these when one
  exists. The remaining 322 operations (181 writes + 141 less-common queries) have no dedicated
  tool by default, but are just as reachable via `clm_invoke` — that's a deliberate design choice to
  keep the always-visible tool list small (registering a named tool for every write across all
  four services would cost ~78,000 tokens of tool-definition context on every request), not a
  coverage gap.
- **Discover before you invoke:** `clm_list_operations(service="...", search="...")` →
  `clm_describe_operation(operation)` → `clm_invoke(operation, params)`. `clm_invoke` validates
  `params` against the real schema before making the HTTP call, so a malformed request fails
  fast with a specific message instead of a wasted round-trip. An operation name like
  `"ShipmentQuery/GetShipmentById"` works unqualified when it's unique across all four services
  (true for every operation today); the fully qualified `"shipment/ShipmentQuery/GetShipmentById"`
  form always works too.
- **A large list response gets truncated, not silently dropped.** If a tool's result has
  `truncated: true`, its `note` field explains why and what to do — usually narrow your date
  range/filters, or pass `fields` to request only the columns you need per row.
- **Status-like filters may need a raw int or the literal placeholder name.** Several shipment
  fields (`ShipmentStatus`, `LeanCardStatus`, `Severity`, ...) have no documented names yet —
  call `clm_list_enums` to see the known integer ranges, and pass either the int or its
  `"UNKNOWN_n"` placeholder name. Construction/team/konshub have similar undocumented int
  fields (e.g. konshub's `ApprovalStatus`, team's `TeamType`) that aren't in `clm_list_enums`
  yet — pass a raw int for those, checked against real data first if possible.
- **`clm_list_material_handovers` requires a non-empty `status`, matched literally** — there
  is no "give me everything" default in the underlying API, and `"All"` is not a wildcard: it
  is accepted without a validation error but matches zero handovers (confirmed live against a
  site with 35 real handovers). Pass a real status value instead — `"InProgress"` is confirmed
  live to filter correctly.
- **Writes go through `clm_invoke` by default — no named write tool is needed.** A named
  `clm_<tag>_command_<operation>` tool only exists if the user set `CLM_WRITE_TOOLS` for that
  service; don't treat a missing one as a coverage gap, use `clm_invoke` instead. If a write
  fails with "CLM_ENABLE_WRITES=false", that's the real read-only gate (independent of
  `CLM_WRITE_TOOLS`) — the user deliberately started this connection unable to mutate data, and
  `clm_invoke` enforces the exact same gate a named tool would. Every write tool's annotations
  tell you its risk regardless of how it's invoked: `destructiveHint: true` means treat it like
  a delete, even if the name doesn't say "delete" — an unrecognized verb defaults to that
  conservative label.
- **A tool error is the model's signal to adapt, not a bug report.** "Shipment not found",
  "'Status' must not be empty", and similar messages come straight from the CLM API (or this
  server's validation) and are meant to be acted on — retry with a corrected parameter, don't
  treat every `ToolError` as something to give up on.
- **If a construction/team/konshub query fails ambiguously (a generic exception, or "site does
  not exist"), try a different `site_id` before concluding the API is broken.** One specific
  staging site has corrupted/incomplete data that breaks several otherwise-healthy operations —
  confirmed by re-running the exact same operation against a properly-configured site and
  getting a clean result both times. See README.md's "Known limitations" for the full,
  corrected list (an earlier pass over-attributed this to universal upstream bugs before a
  broader test caught the real cause).
- **`TimelineQuery/GetShipmentTimelineById` 500s for a genuinely nonexistent shipment id** —
  confirmed independent of site, this one is a real "should be a 404" defect.
  `clm_get_shipment_timeline` surfaces it faithfully as a `ToolError`.
- **`clm_list_site_responsible_persons`'s `total_count` doesn't track its real row count** —
  a genuine, reproducible API defect; trust `data`/`returned` instead.
- **`clm_get_material_usage` is slow (3–12s observed) and always succeeds despite the API's own
  `IsSuccess: false` flag** — handled transparently (a populated result means it worked; only a
  genuinely nonexistent material still errors), just don't be surprised by the latency.
- **Team/konshub curated list tools, plus `clm_list_materials`, use one-based pagination**
  (`page_number=1` is the first page) — different from shipment's zero-based convention. The
  default is already correct per tool; only matters if you override `page_number` explicitly.
- **`clm_get_cockpit_weekly_counts` refuses a date window over 92 days** — the API returns one
  row per day with no cap of its own, so pass a narrow range (it's a "weekly" view; a few months
  is already generous) rather than a multi-year span.
