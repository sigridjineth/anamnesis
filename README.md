# Anamnesis

**Anamnesis** is a Python package and local runtime for **capturing, normalizing, storing, and querying agent activity** across:

- **Claude Code**
- **Codex**
- **OpenCode**

It is designed around one practical idea:

> different agent runtimes emit different raw events, but you can still give all of them **one shared memory substrate** and **one shared query surface**.

This repository currently provides that shared layer as:

1. a **canonical SQLite raw store**
2. a set of **product-specific ingestion adapters**
3. a **shared query service**
4. a **single MCP server** that exposes the memory to any compatible client
5. an **initializer CLI** that writes deployable local config for Claude, Codex, and OpenCode

It also includes optional bridges to:

- **UQA** for richer search/index sidecars
- **Flex-style SQLite cells** for compatibility and fallback exploration

---

> **Naming note**
>
> The project/distribution/CLI brand is **Anamnesis**.
> The Python import path remains `agent_memory` for compatibility in this revision.

## Table of contents

- [What this project is](#what-this-project-is)
- [Current status](#current-status)
- [Why this exists](#why-this-exists)
- [High-level architecture](#high-level-architecture)
- [Repository layout](#repository-layout)
- [Supported clients and ingestion paths](#supported-clients-and-ingestion-paths)
- [Core concepts](#core-concepts)
  - [Canonical raw store](#canonical-raw-store)
  - [Canonical event model](#canonical-event-model)
  - [Query layer](#query-layer)
  - [MCP surface](#mcp-surface)
  - [Optional UQA sidecar](#optional-uqa-sidecar)
  - [Optional Flex bridge](#optional-flex-bridge)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Development install](#development-install)
  - [Runtime install](#runtime-install)
  - [Optional MCP dependency](#optional-mcp-dependency)
- [Quick start](#quick-start)
- [Bootstrap generated config with `anamnesis-init`](#bootstrap-generated-config-with-anamnesis-init)
  - [What it writes](#what-it-writes)
  - [Important Codex note](#important-codex-note)
- [Direct ingestion](#direct-ingestion)
- [Client-specific capture and backfill](#client-specific-capture-and-backfill)
  - [Claude Code](#claude-code)
  - [Codex](#codex)
  - [OpenCode](#opencode)
- [Querying memory](#querying-memory)
  - [`MemoryService`](#memoryservice)
  - [MCP tools](#mcp-tools)
  - [Read-only SQL behavior](#read-only-sql-behavior)
- [Running the MCP server](#running-the-mcp-server)
  - [stdio](#stdio)
  - [SSE](#sse)
  - [Streamable HTTP](#streamable-http)
- [Configuration reference](#configuration-reference)
  - [Core runtime environment variables](#core-runtime-environment-variables)
  - [MCP server environment variables](#mcp-server-environment-variables)
- [Python API example](#python-api-example)
- [Canonical SQLite schema](#canonical-sqlite-schema)
- [Examples](#examples)
- [Deployment notes](#deployment-notes)
- [Verification and test status](#verification-and-test-status)
- [Known limitations](#known-limitations)
- [Non-goals](#non-goals)
- [Roadmap](#roadmap)
- [Related documents](#related-documents)

---

## What this project is

**Anamnesis** is **not** a full standalone database and **not** a replacement for Claude/Codex/OpenCode themselves.

It is the layer that sits **under** those tools and gives you:

- one place to **store normalized session activity**
- one way to **query that activity later**
- one way to expose that memory back to agents through **MCP**

In practice, that means you can do things like:

- capture Claude prompts, tool activity, and session end markers
- import Codex prompt history and transcript tool calls
- import OpenCode exported sessions and live plugin events
- query all of that through one Python API or one MCP server
- optionally build a richer **UQA-backed sidecar** on top of the canonical store

---

## Current status

This project is currently a **deployable local package baseline**.

That means:

- the package can be built into **sdist** and **wheel** artifacts
- the wheel can be installed into a clean virtual environment
- the initializer can generate usable local client configuration
- ingest → store → query → MCP server flow works end to end
- the MCP server supports:
  - `stdio`
  - `sse`
  - `streamable-http`

It does **not** mean every advanced long-term goal is complete yet.

Examples of what is already real:

- canonical SQLite storage
- adapters for Claude, Codex, and OpenCode
- Codex history/session backfill
- OpenCode export/session backfill
- MCP tools for orient/search/trace/digest/sql/health
- deployable initializer CLI
- optional UQA sidecar bridge

Examples of what is still future-facing:

- deeper graph enrichment in the UQA sidecar
- true embedding-powered vector search workflows
- richer shared skills packaging
- production polish around type/lint baselines and release automation

---

## Why this exists

Claude Code, Codex, and OpenCode do **not** share the same hook model, plugin model, or transcript format.

Trying to force them into one fake “plugin abstraction” usually creates brittle code.

The better abstraction is:

- **product-specific capture on the way in**
- **shared MCP/query surface on the way out**

That is the central design choice of this repo.

---

## High-level architecture

```text
Claude hooks  ─┐
Codex hooks   ─┼─> adapters -> canonical events -> SQLite raw store
OpenCode      ─┘
                                       │
                                       ├─> MemoryQueryService / MemoryService
                                       │
                                       ├─> MCP server
                                       │
                                       ├─> optional UQA sidecar
                                       │
                                       └─> optional Flex-style discovery/fallback
```

More concretely:

1. **Adapters** normalize raw payloads into canonical event objects.
2. **RawMemoryStore** persists those events in SQLite.
3. **MemoryQueryService** provides search, trace, digest, and SQL helpers.
4. **MemoryService** is the top-level orchestration layer.
5. **MCP server** exposes that service as tools.
6. **UQA sidecar** can optionally project the raw store into richer search infrastructure.

---

## Repository layout

```text
agent_memory/
  adapters/           Product-specific payload normalizers
  backends/           Backend-specific query helpers
  hooks/              Thin client hook entrypoints
  providers/          Flex/UQA provider abstractions
  sync/               Projection helpers (for example Flex -> UQA)
  config.py           Environment-driven runtime configuration
  codex_sync.py       Codex history + transcript backfill/import
  opencode_sync.py    OpenCode export/session backfill/import
  ingest.py           Direct ingestion CLI
  init_cli.py         Deployable config/bootstrap CLI
  mcp_server.py       MCP server entrypoint
  models.py           Canonical event and query models
  query.py            Canonical query helpers
  service.py          Top-level service API
  storage.py          Raw SQLite store

examples/clients/
  claude/             Claude MCP + hook examples
  codex/              Codex MCP + hook examples
  opencode/           OpenCode MCP + plugin examples

docs/
  ARCHITECTURE.md     Layering and design notes
  DEPLOYMENT.md       Deployment baseline and rollout notes

tests/
  ...                 stdlib unittest coverage

uqa/                  Upstream UQA checkout (kept read-only)
flex/                 Upstream Flex checkout (kept read-only)
```

Two important principles:

- the **upstream clones** are intentionally kept read-only
- the **`agent_memory/` package** is where the shared abstraction lives

---

## Supported clients and ingestion paths

| Client | Live capture path | Backfill path | Query path |
| --- | --- | --- | --- |
| Claude Code | hook command -> `agent_memory.hooks.claude` | none yet beyond hook-fed raw DB | MCP |
| Codex | hook command -> `agent_memory.hooks.codex` | `agent_memory.codex_sync` from `~/.codex/history.jsonl` and `~/.codex/sessions/` | MCP |
| OpenCode | plugin -> `agent_memory.hooks.opencode` | `agent_memory.opencode_sync` from `opencode export` or saved exports | MCP |

The supported query surface is shared across all three through the package API and the MCP server.

---

## Core concepts

### Canonical raw store

The SQLite raw store is the **source of truth**.

It stores normalized events in a compact schema that is easy to inspect and rebuild from.

This is important because:

- client payload formats can change
- richer indexes can be rebuilt later
- debugging is easier when the first durable layer is simple SQLite

### Canonical event model

The canonical event model currently supports these agent kinds:

- `claude`
- `codex`
- `opencode`

And these event kinds:

- `prompt`
- `assistant_message`
- `tool_call`
- `tool_result`
- `permission`
- `file_touch`
- `session_state`

Each event carries normalized fields like:

- `id`
- `agent`
- `session_id`
- `project_id`
- `ts`
- `kind`
- `role`
- `content`
- `tool_name`
- `target_path`
- `payload`

### Query layer

The query layer is exposed through:

- `MemoryQueryService` — lower-level query helper over a concrete store
- `MemoryService` — higher-level orchestration layer that chooses the right backend behavior

Key query operations:

- `orient()` — inspect schema and counts
- `search()` — search text/tool/path fields, optionally via UQA sidecar
- `trace_file()` — inspect file touches over time
- `trace_decision()` — find session-level traces for a topic
- `digest()` — summarize recent sessions and top files
- `sql()` — run read-only SQL against the canonical store
- `rebuild_uqa_sidecar()` — project raw data into a UQA sidecar

### MCP surface

The MCP server exposes the shared query layer to agent runtimes.

Tool names:

- `memory_health`
- `memory_orient`
- `memory_search`
- `memory_trace_file`
- `memory_trace_decision`
- `memory_digest`
- `memory_sql`
- `memory_rebuild_uqa_sidecar`

### Optional UQA sidecar

When local UQA checkout + dependencies are available, the package can project the canonical raw store into a sidecar database and run richer retrieval there.

The current design goal is:

- raw store remains the source of truth
- UQA sidecar is rebuildable
- richer indexing is optional, not mandatory

### Optional Flex bridge

The package can also discover or inspect Flex-like SQLite cells.

That is useful for:

- fallback exploration
- compatibility with a cell-style workflow
- mixed environments where you want raw canonical store + Flex-style inspection

---

## Requirements

Minimum runtime requirements:

- **Python 3.12+**

Core package runtime:

- **stdlib-only** by default

Optional runtime dependency:

- `mcp>=1.0.0` when running the MCP server

Optional local integrations:

- local UQA checkout + its Python dependencies
- local Flex checkout when using Flex discovery/helpers
- local `codex` CLI when registering Codex MCP automatically
- local `opencode` CLI when using OpenCode session export backfill

---

## Installation

### Development install

If you are working directly in this repo:

```bash
python3 -m pip install -e .
```

This gives you:

- editable Python package install
- all console scripts from `pyproject.toml`
- local development iteration without rebuilding the wheel each time

### Runtime install

If you want a normal install from a built wheel:

```bash
python3 -m pip install dist/anamnesis-0.1.0-py3-none-any.whl
```

You can also build artifacts first:

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install build
python -m build --sdist --wheel
```

Artifacts are written to:

- `dist/anamnesis-0.1.0-py3-none-any.whl`
- `dist/anamnesis-0.1.0.tar.gz`

### Optional MCP dependency

If you want to run the MCP server, install the optional extra:

```bash
python3 -m pip install -e '.[mcp]'
```

Or in an already-installed environment:

```bash
python -m pip install 'mcp>=1.0.0'
```

---

## Quick start

This is the fastest end-to-end local path.

### 1. Verify the repo state

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall agent_memory tests
```

### 2. Install the package

```bash
python3 -m pip install -e .
python3 -m pip install -e '.[mcp]'
```

### 3. Generate local client config

```bash
anamnesis-init --workspace-root "$PWD"
```

### 4. Ingest one sample event

```bash
cat > /tmp/anamnesis-payload.json <<'JSON'
{"event":"UserPromptSubmit","session_id":"demo-session","project":"demo-project","timestamp":"2026-03-08T00:00:00Z","prompt":"hello memory"}
JSON

anamnesis-ingest \
  --agent claude \
  --db .anamnesis/anamnesis.db \
  --input /tmp/anamnesis-payload.json
```

### 5. Query it back from Python

```bash
ANAMNESIS_DB=.anamnesis/anamnesis.db python3 - <<'PY'
from agent_memory.service import MemoryService
service = MemoryService()
print(service.orient(project_id="demo-project"))
print(service.search("hello", project_id="demo-project"))
PY
```

### 6. Run the MCP server

```bash
ANAMNESIS_DB=.anamnesis/anamnesis.db \
python3 -m agent_memory.mcp_server
```

At that point a compatible MCP client can query the same memory database.

---

## Bootstrap generated config with `anamnesis-init`

`anamnesis-init` is the main bootstrap command that turns the package into a practical local deployment.

CLI help:

```bash
anamnesis-init --help
```

Basic usage:

```bash
anamnesis-init --workspace-root "$PWD"
```

Limit to one client:

```bash
anamnesis-init --workspace-root "$PWD" --client claude
anamnesis-init --workspace-root "$PWD" --client codex
anamnesis-init --workspace-root "$PWD" --client opencode
```

Use a custom database path:

```bash
anamnesis-init \
  --workspace-root "$PWD" \
  --db-path "$PWD/.anamnesis/my-memory.db"
```

Use a custom Python executable:

```bash
anamnesis-init \
  --workspace-root "$PWD" \
  --python /path/to/python3
```

### What it writes

By default the initializer writes or updates:

- project `.gitignore`
  - appends `.anamnesis/`
- Claude project config
  - `.mcp.json`
  - `.claude/settings.local.json`
- Codex config
  - `~/.codex/settings.json` or the path implied by `--codex-home`
  - `.anamnesis/generated/register-codex-mcp.sh`
- OpenCode project config
  - `.opencode/opencode.json`
  - `.opencode/plugins/anamnesis.ts`

Behavior notes:

- existing JSON config is **merged**, not blindly replaced
- generated Codex registration script is **idempotent-friendly** because it removes the existing `Anamnesis` MCP entry first
- OpenCode plugin file is generated from the Python-side init logic so it matches the current runtime command path

### Important Codex note

If you want the initializer to run Codex MCP registration immediately:

```bash
anamnesis-init --workspace-root "$PWD" --register-codex
```

If you override `--codex-home`, then for `--register-codex` you must point it at a directory literally named `.codex`.

Why?

Because automatic Codex registration works by redirecting `HOME`, and Codex expects its config under `HOME/.codex`.

Good example:

```bash
anamnesis-init \
  --workspace-root "$PWD" \
  --codex-home /tmp/test-home/.codex \
  --register-codex
```

Bad example:

```bash
anamnesis-init \
  --workspace-root "$PWD" \
  --codex-home /tmp/test-home/custom-codex \
  --register-codex
```

The second form is intentionally rejected.

---

## Direct ingestion

The direct ingestion CLI is the lowest-friction way to append events into the canonical store.

```bash
anamnesis-ingest --help
```

Basic pattern:

```bash
cat payload.json | anamnesis-ingest --agent claude --db .anamnesis/anamnesis.db
```

Supported agent types:

- `claude`
- `codex`
- `opencode`

Useful options:

- `--input` — read JSON or JSONL from a file instead of stdin
- `--session-id` — inject a missing session id
- `--project-id` — inject a missing project id
- `--quiet` — suppress JSON summary output

Accepted payload forms:

- one JSON object
- one JSON array of objects
- JSONL with one object per line

---

## Client-specific capture and backfill

## Claude Code

Live capture is done through hook commands that call:

```bash
python3 -m agent_memory.hooks.claude --db ... --quiet
```

The example config captures:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `SessionEnd`

Reference files:

- `examples/clients/claude/.mcp.json`
- `examples/clients/claude/settings.local.json`
- `examples/clients/claude/HOOKS.md`

Recommended behavior:

- keep hook writes pointed at the same `.anamnesis/anamnesis.db`
- keep MCP reads pointed at that same database

That way ingestion and querying share one canonical store.

## Codex

Codex has two supported paths.

### 1. Live capture

Hook wrapper:

```bash
python3 -m agent_memory.hooks.codex --db ... --quiet
```

Typical captured surfaces:

- `UserPromptSubmit`
- `PostToolUse`

The Codex adapter expands some tool events into:

- `tool_call`
- `tool_result`

### 2. Backfill prior history and transcripts

```bash
anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

Default input sources:

- `~/.codex/history.jsonl`
- `~/.codex/sessions/`

Useful options:

- `--history`
- `--sessions-root`
- `--skip-history`
- `--skip-sessions`
- `--include-user-messages`
- `--project-id`
- `--quiet`

Important note:

By default transcript user messages are skipped to avoid duplicating prompt history that already exists in `history.jsonl`.

Reference files:

- `examples/clients/codex/config.toml`
- `examples/clients/codex/settings.json`
- `examples/clients/codex/HOOKS.md`

## OpenCode

OpenCode also has two supported paths.

### 1. Live plugin capture

The generated plugin or example plugin emits JSON into:

```bash
python3 -m agent_memory.hooks.opencode --db ... --quiet
```

Typical captured surfaces:

- `chat.message`
- `tool.execute.before`
- `tool.execute.after`
- `message.part.updated`
- `file.edited`
- `session.idle`

### 2. Backfill exported sessions

```bash
anamnesis-opencode-sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

Supported import paths:

- export a specific session with `--session-id`
- import saved exports with `--export-file`
- discover sessions with `--all-sessions`
- auto-discover visible sessions when neither explicit ids nor export files are provided

Useful options:

- `--session-id`
- `--export-file`
- `--all-sessions`
- `--limit`
- `--project-id`
- `--quiet`

Important note:

Some real OpenCode exports can be malformed. The importer is best-effort:

- valid exports are imported
- invalid ones are reported under `failures`
- the whole run does not fail just because one export is bad

Reference files:

- `examples/clients/opencode/opencode.json`
- `examples/clients/opencode/anamnesis.ts`
- `examples/clients/opencode/HOOKS.md`

---

## Querying memory

There are two primary ways to query memory:

1. Python API
2. MCP tools

### `MemoryService`

`MemoryService` is the main high-level interface.

Methods:

- `health()`
- `ingest(events)`
- `orient()`
- `search(query)`
- `trace_file(path)`
- `trace_decision(query)`
- `digest(days=7)`
- `sql(sql, read_only=True)`
- `rebuild_uqa_sidecar()`

Behavior notes:

- when the target DB is the canonical raw store, queries run against canonical tables
- when the target DB looks like a non-canonical SQLite cell, the service can fall back to generic/Flex-like exploration
- `search()` can use the UQA sidecar when available and useful

### MCP tools

The MCP server exposes these tools:

- `memory_health`
  - runtime summary, database path, sidecar status, detected Flex cells
- `memory_orient`
  - table list, counts, time window, agent distribution
- `memory_search`
  - canonical search or UQA-backed search when available
- `memory_trace_file`
  - recent file touches for a path
- `memory_trace_decision`
  - session-level aggregation for a topic query
- `memory_digest`
  - recent session summary and top files
- `memory_sql`
  - read-only SQL
- `memory_rebuild_uqa_sidecar`
  - rebuild optional UQA projection

### Read-only SQL behavior

For the canonical store, `MemoryService.sql()` intentionally rejects mutation.

Allowed patterns are effectively limited to read-only SQL such as:

- `SELECT ...`
- `WITH ...`
- `EXPLAIN ...`
- `PRAGMA ...`

This is a deliberate safety choice.

---

## Running the MCP server

```bash
python3 -m agent_memory.mcp_server --help
```

The MCP server supports three transports.

### stdio

Best for local client integration.

```bash
ANAMNESIS_DB=.anamnesis/anamnesis.db \
python3 -m agent_memory.mcp_server
```

### SSE

Useful when you want a network-exposed MCP server in environments still using SSE.

```bash
ANAMNESIS_DB=.anamnesis/anamnesis.db \
python3 -m agent_memory.mcp_server \
  --transport sse \
  --host 0.0.0.0 \
  --port 8000 \
  --mount-path /anamnesis
```

### Streamable HTTP

Recommended for a more deployable HTTP shape.

```bash
ANAMNESIS_DB=.anamnesis/anamnesis.db \
PORT=8000 \
python3 -m agent_memory.mcp_server --transport streamable-http
```

Default behavior:

- `stdio` defaults to host `127.0.0.1`
- HTTP transports default to host `0.0.0.0`
- port resolution order is:
  - `--port`
  - `ANAMNESIS_MCP_PORT`
  - `PORT`
  - `8000`
- default streamable HTTP path is `/mcp`

Useful flags:

- `--transport`
- `--host`
- `--port`
- `--mount-path`
- `--sse-path`
- `--message-path`
- `--streamable-http-path`
- `--log-level`
- `--debug`
- `--json-response`
- `--stateless-http`

---

## Configuration reference

### Core runtime environment variables

These are consumed by `agent_memory.config.Settings`.

| Variable | Meaning |
| --- | --- |
| `ANAMNESIS_DB` | canonical raw SQLite database path |
| `ANAMNESIS_UQA_SIDECAR` | optional UQA sidecar database path |
| `FLEX_REPO_ROOT` | local Flex checkout path |
| `UQA_REPO_ROOT` | local UQA checkout path |
| `FLEX_CELL` | default Flex cell name |
| `FLEX_CELL_PATH` | direct path to a Flex-style SQLite file |
| `ANAMNESIS_LIMIT` | default query limit |

### MCP server environment variables

These affect `agent_memory.mcp_server`.

| Variable | Meaning |
| --- | --- |
| `ANAMNESIS_MCP_TRANSPORT` | `stdio`, `sse`, or `streamable-http` |
| `ANAMNESIS_MCP_HOST` | HTTP bind host |
| `ANAMNESIS_MCP_PORT` | HTTP bind port |
| `PORT` | generic fallback port |
| `ANAMNESIS_MCP_MOUNT_PATH` | base mount path for SSE |
| `ANAMNESIS_MCP_SSE_PATH` | SSE endpoint path |
| `ANAMNESIS_MCP_MESSAGE_PATH` | POST path paired with SSE |
| `ANAMNESIS_MCP_STREAMABLE_HTTP_PATH` | streamable HTTP endpoint path |
| `ANAMNESIS_MCP_LOG_LEVEL` | FastMCP log level |
| `ANAMNESIS_MCP_DEBUG` | enable debug mode |
| `ANAMNESIS_MCP_JSON_RESPONSE` | enable JSON responses where supported |
| `ANAMNESIS_MCP_STATELESS_HTTP` | enable stateless streamable HTTP mode |

---

## Python API example

```python
from agent_memory.service import MemoryService

service = MemoryService()

print(service.health())
print(service.orient(project_id="my-project"))
print(service.search("install script", project_id="my-project", limit=5))
print(service.trace_file("src/app.py", limit=20))
print(service.trace_decision("curl install", limit=10))
print(service.digest(days=7))
print(service.sql("SELECT COUNT(*) AS n FROM events"))
```

If you want to rebuild the optional UQA sidecar:

```python
from agent_memory.service import MemoryService

service = MemoryService()
print(service.rebuild_uqa_sidecar())
```

---

## Canonical SQLite schema

The raw store creates three main tables.

### `sessions`

Tracks session-level metadata.

Columns:

- `session_id` — primary key
- `agent`
- `project_id`
- `started_at`
- `ended_at`
- `metadata_json`

### `events`

Stores normalized event rows.

Columns:

- `id` — primary key
- `agent`
- `session_id`
- `project_id`
- `ts`
- `kind`
- `role`
- `content`
- `tool_name`
- `target_path`
- `payload_json`

### `file_touches`

Tracks file-path activity derived from events.

Columns:

- `event_id`
- `path`
- `operation`

Indexes are created for:

- session/time lookups
- project/time lookups
- kind/time lookups
- file path lookups

---

## Examples

Reference client config lives under `examples/clients/`.

Claude:

- `examples/clients/claude/.mcp.json`
- `examples/clients/claude/settings.local.json`
- `examples/clients/claude/HOOKS.md`

Codex:

- `examples/clients/codex/config.toml`
- `examples/clients/codex/settings.json`
- `examples/clients/codex/HOOKS.md`

OpenCode:

- `examples/clients/opencode/opencode.json`
- `examples/clients/opencode/anamnesis.ts`
- `examples/clients/opencode/HOOKS.md`

These are useful for understanding the generated shape, but the preferred setup path is now:

```bash
anamnesis-init --workspace-root "$PWD"
```

---

## Deployment notes

This repository already supports a practical local deployment flow.

### Build artifacts

```bash
python -m build --sdist --wheel
```

Expected outputs:

- `dist/anamnesis-0.1.0-py3-none-any.whl`
- `dist/anamnesis-0.1.0.tar.gz`

### Recommended database placement

For deployment, point `ANAMNESIS_DB` at a persistent writable path outside the source checkout.

Example:

```bash
export ANAMNESIS_DB=/srv/anamnesis/anamnesis.db
```

### Typical HTTP deployment command

```bash
ANAMNESIS_DB=/srv/anamnesis/anamnesis.db \
PORT=8000 \
python -m agent_memory.mcp_server --transport streamable-http
```

### Operational cautions

- SQLite file permissions matter
- optional UQA/Flex integrations should be treated as local extras, not guaranteed runtime dependencies
- if you run a long-lived MCP HTTP service, add your own process supervision
- OpenCode export quality can vary, so expect occasional skipped backfill failures

For more deployment-specific notes, read:

- `docs/DEPLOYMENT.md`

---

## Verification and test status

Current local verification baseline in this repo:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall agent_memory tests
python3 -m agent_memory.init_cli --help
python3 -m agent_memory.ingest --help
python3 -m agent_memory.codex_sync --help
python3 -m agent_memory.opencode_sync --help
python3 -m agent_memory.mcp_server --help
```

Recent verified paths include:

- full unittest pass
- compileall pass
- wheel and sdist build pass in a clean venv
- wheel install pass in a clean venv
- `anamnesis-init` run pass
- ingest + query smoke pass
- `create_server()` smoke pass with MCP installed
- streamable HTTP config/build path smoke pass

---

## Known limitations

This package is useful now, but it is still intentionally conservative.

Known limitations include:

- no first-class release automation yet
- no container image included yet
- no production process manager config included yet
- OpenCode exports can be malformed and are imported best-effort
- UQA integration is optional and depends on local checkout/importability
- Flex integration is optional and discovery-oriented
- mutation through canonical `MemoryService.sql()` is intentionally blocked
- richer graph/vector semantics are not complete yet

---

## Non-goals

At the current stage, this project is **not** trying to be:

- a production SaaS memory backend
- a full replacement for Flex or UQA
- a write-heavy transactional application database
- a universal plugin SDK for every agent runtime
- an audited security boundary for untrusted remote clients

It is trying to be a **clean, practical shared memory/query layer**.

---

## Roadmap

Near-term likely improvements:

1. clean up current typing and lint follow-up issues
2. enrich UQA sidecar projection with graph/vector-friendly materialization
3. add higher-level presets that compile to SQL/UQA automatically
4. improve packaging around release automation and distribution
5. expand shared client UX around prebuilt skills/workflows
6. improve robustness around malformed upstream export formats

---

## Related documents

- `ARCHITECTURE.md` — concise architecture overview
- `docs/ARCHITECTURE.md` — layering and design notes
- `docs/DEPLOYMENT.md` — deployment baseline, risks, and release notes
- `examples/clients/` — reference config for Claude, Codex, and OpenCode

---

If you want to understand the package from the shortest useful path, do this:

```bash
python3 -m pip install -e .
python3 -m pip install -e '.[mcp]'
anamnesis-init --workspace-root "$PWD"
python3 -m unittest discover -s tests -v
ANAMNESIS_DB=.anamnesis/anamnesis.db python3 -m agent_memory.mcp_server
```

That will get you from source checkout to a working local memory service quickly.
