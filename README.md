# Anamnesis

**Anamnesis** is a **UQA-native shared memory layer** for **Claude Code**, **Codex**, and **OpenCode**.

It captures agent activity into a canonical SQLite raw store, then **always** projects that store into a **UQA** sidecar and serves queries through **UQA**.

This repository is intentionally opinionated now:

- **UQA is mandatory**
- **Anamnesis is UQA-only at query time**
- **there is no non-UQA runtime path**
- **there is no Flex fallback path inside the product surface**

---

## Table of contents

- [What Anamnesis is](#what-anamnesis-is)
- [Hard requirements](#hard-requirements)
- [Current status](#current-status)
- [Architecture](#architecture)
- [What gets captured](#what-gets-captured)
- [What you can query](#what-you-can-query)
- [Flex parity assessment](#flex-parity-assessment)
- [UQA dependency assessment](#uqa-dependency-assessment)
- [Repository layout](#repository-layout)
- [Install with uv](#install-with-uv)
- [Quickstart](#quickstart)
- [Client setup](#client-setup)
  - [Claude Code](#claude-code)
  - [Codex](#codex)
  - [OpenCode](#opencode)
- [CLI reference](#cli-reference)
- [Python API](#python-api)
- [Data model](#data-model)
- [Environment variables](#environment-variables)
- [Release with uv](#release-with-uv)
- [Verification](#verification)
- [Known limitations](#known-limitations)

---

## What Anamnesis is

Anamnesis is a memory/query layer for agentic coding environments.

It does three things:

1. **capture** tool- and session-level events from Claude Code, Codex, and OpenCode
2. **normalize** those events into one canonical raw SQLite schema
3. **query** that normalized memory through **UQA** so the same memory surface is available to every client

The core design is:

- **product-specific ingestion on the way in**
- **one canonical raw schema in the middle**
- **UQA-only querying on the way out**

That gives you one shared memory surface across multiple agents without pretending those agents all emit the same hook format.

---

## Hard requirements

These are not optional in the current design:

- **UQA is required**
- the query layer is **always backed by UQA**
- the raw SQLite store is **source-of-truth storage**, not the final query engine
- if UQA is unavailable, the system should be treated as **misconfigured**, not as a supported fallback mode

This repo is prepared around that assumption.

---

## Current status

What is already implemented:

- canonical raw SQLite storage
- hook/event adapters for:
  - Claude Code
  - Codex
  - OpenCode
- Codex backfill from:
  - `~/.codex/history.jsonl`
  - `~/.codex/sessions/`
- OpenCode backfill from exported sessions
- UQA sidecar rebuild from the canonical raw store
- MCP server for:
  - `memory_health`
  - `memory_orient`
  - `memory_search`
  - `memory_trace_file`
  - `memory_trace_decision`
  - `memory_digest`
  - `memory_sql`
  - `memory_rebuild_uqa_sidecar`
- deployable init CLI for Claude/Codex/OpenCode
- uv-first release workflow
- uv workspace setup where local `uqa/` is a workspace member and git submodule

What this repo does **not** currently claim:

- full getflex.dev feature parity
- full graph enrichment parity with Flex
- complete vector/embedding parity with Flex’s broader retrieval story
- production-grade multi-tenant service packaging

---

## Architecture

```text
Claude hooks   ─┐
Codex hooks    ─┼─> adapters -> canonical raw SQLite -> UQA sidecar -> MCP / Python API
OpenCode hooks ─┘
```

### Storage layers

#### 1. Canonical raw store

The raw store is plain SQLite and exists to make ingestion simple, durable, and inspectable.

It contains:

- `sessions`
- `events`
- `file_touches`

#### 2. Mandatory UQA sidecar

The sidecar is rebuilt from the raw store and is the **only supported query backend**.

Anamnesis uses UQA for:

- search
- schema orientation
- digest queries
- decision tracing
- file trace reconstruction
- read-only SQL surface

### Why the raw store still exists

The raw store is not a competing query engine.

It exists because:

- hooks should append to something simple and robust
- normalization should remain easy to debug
- UQA projection should be rebuildable
- raw evidence should stay inspectable even if the query schema evolves

---

## What gets captured

### Claude Code

The provided examples and hook wrapper cover:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `SessionEnd`

### Codex

The live + backfill path covers:

- hook prompt payloads
- `PostToolUse` payloads
- `history.jsonl`
- session transcript messages
- `function_call`
- `function_call_output`

### OpenCode

The live + backfill path covers:

- `chat.message`
- `tool.execute.before`
- `tool.execute.after`
- `message.part.updated`
- `file.edited`
- `session.*`
- exported session documents from `opencode export`

---

## What you can query

Through the Python API or MCP layer, you can:

- inspect the schema with `orient`
- run full-text UQA-backed search over normalized event content
- trace file history from normalized file touches
- trace decision/session clusters for a concept
- build recent digests by session and touched file
- run read-only SQL against the UQA sidecar

Examples:

```sql
SELECT id, session_id, ts, kind, content
FROM events
WHERE text_match(content, 'install script')
ORDER BY _score DESC
LIMIT 10;
```

```sql
SELECT session_id, COUNT(*) AS event_count
FROM events
GROUP BY session_id
ORDER BY event_count DESC;
```

---

## Flex parity assessment

**No — Anamnesis does not currently implement 100% of the feature surface described on getflex.dev.**

That conclusion is based on both:

- the current public Flex docs/site, and
- the current local Anamnesis codebase

### Flex capabilities publicly described on getflex.dev

The current getflex.dev pages describe, among other things:

- a dedicated Flex compile pipeline
- queue/worker/service management (`queue.db`, launchd/systemd, background worker)
- embedding model download and embedding workflows
- `keyword()` + semantic retrieval + modulation-token style embedding operations
- enrichment cycles writing `_enrich_*`
- convention-heavy module system with `_raw_*`, `_edges_*`, `_types_*`, `_enrich_*`
- broader preset family like:
  - `@orient`
  - `@digest`
  - `@file`
  - `@story`
  - `@sprints`
  - `@genealogy`
  - `@delegation-tree`
  - `@bridges`
  - `@file-search`
  - `@health`
- richer identity/lineage surfaces such as:
  - file identity
  - repo identity
  - content identity
  - URL identity

### What Anamnesis actually has today

Anamnesis currently has:

- its own canonical raw schema (`sessions`, `events`, `file_touches`)
- client adapters for Claude/Codex/OpenCode
- UQA-backed sidecar projection
- MCP tools for search/orient/trace/digest/sql
- init/backfill/release tooling

### What is missing relative to full Flex parity

Anamnesis does **not** currently ship all of the following as product-complete features:

- Flex’s full module/table convention system
- Flex’s full queue/daemon/service lifecycle
- Flex’s full enrichment pipeline and `_enrich_*` family
- Flex’s named preset set at full parity
- Flex’s identity-edge family at full parity
- Flex’s broader semantic retrieval/modulation workflow as described on the site
- Flex’s full compile/runtime packaging model

So the correct statement today is:

> **Anamnesis is influenced by Flex and can be compared to Flex, but it is not a 100% implementation of the current getflex.dev feature surface.**

---

## UQA dependency assessment

**Yes — the current project direction is UQA-only and intentionally UQA-dependent.**

Concretely:

- runtime querying is routed through UQA
- the package metadata declares `uqa>=0.2.1`
- the local repo uses `uqa/` as a uv workspace member
- release tooling builds **both** `uqa` and `anamnesis`
- release verification installs **both** wheels into a clean uv-managed venv
- the local repository now tracks `uqa/` as a git submodule

That means the intended release story is:

1. publish `uqa`
2. publish `anamnesis`
3. install `anamnesis` as a package that depends on `uqa`

---

## Repository layout

```text
anamnesis/
  adapters/         client-specific normalizers
  hooks/            thin hook entrypoints
  config.py         runtime settings
  storage.py        canonical raw SQLite store
  uqa_sidecar.py    raw -> UQA sidecar projection and UQA query helpers
  query.py          query service facade
  service.py        top-level application service
  init_cli.py       client config/bootstrap writer
  mcp_server.py     FastMCP entrypoint
  codex_sync.py     Codex backfill
  opencode_sync.py  OpenCode backfill

docs/
  ARCHITECTURE.md
  DEPLOYMENT.md
  RELEASE_UV.md

examples/clients/
  claude/
  codex/
  opencode/

scripts/
  release_uv.py
  verify_uv_release.py

uqa/                tracked git submodule + uv workspace member
flex/               local comparison checkout, ignored from this repo
```

---

## Install with uv

### Clone the repo

Because `uqa/` is a tracked submodule, clone with submodules:

```bash
git clone --recurse-submodules https://github.com/sigridjineth/anamnesis.git
cd anamnesis
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

### Sync the workspace

```bash
uv sync --all-packages --group dev
```

That installs:

- `anamnesis`
- workspace member `uqa`
- the dev group (currently including the MCP SDK for local server work)

---

## Quickstart

### 1. Initialize client config

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

### 2. Ingest a sample Claude payload

```bash
cat <<'JSON' | uv run anamnesis-ingest --agent claude --db .anamnesis/anamnesis.db
{"event":"UserPromptSubmit","session_id":"demo-session","project":"demo-project","timestamp":"2026-03-08T00:00:00Z","prompt":"why did we create the install script?"}
JSON
```

### 3. Query through the Python API

```bash
uv run python - <<'PY'
from anamnesis.service import MemoryService
service = MemoryService()
print(service.search("install script"))
PY
```

### 4. Or start the MCP server

```bash
uv run anamnesis-mcp
```

---

## Client setup

## Claude Code

Generated by:

```bash
uv run anamnesis-init --clients claude
```

Artifacts:

- `.mcp.json`
- `.claude/settings.local.json`

## Codex

Generated by:

```bash
uv run anamnesis-init --clients codex
```

Artifacts:

- `~/.codex/settings.json` or custom `--codex-home`
- `.anamnesis/generated/register-codex-mcp.sh`

Optional direct registration:

```bash
uv run anamnesis-init --clients codex --register-codex --codex-home ~/.codex
```

## OpenCode

Generated by:

```bash
uv run anamnesis-init --clients opencode
```

Artifacts:

- `.opencode/opencode.json`
- `.opencode/plugins/anamnesis.ts`

---

## CLI reference

### `anamnesis-init`

Write client config for Claude/Codex/OpenCode.

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

### `anamnesis-ingest`

Normalize hook payloads and append them to the raw SQLite store.

```bash
uv run anamnesis-ingest --agent claude --db .anamnesis/anamnesis.db --input payload.json
```

### `anamnesis-codex-sync`

Backfill Codex history and transcript artifacts.

```bash
uv run anamnesis-codex-sync --db .anamnesis/anamnesis.db
```

### `anamnesis-opencode-sync`

Backfill OpenCode exported sessions.

```bash
uv run anamnesis-opencode-sync --db .anamnesis/anamnesis.db --all-sessions
```

### `anamnesis-mcp`

Run the MCP server.

```bash
uv run anamnesis-mcp --transport stdio
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

---

## Python API

```python
from anamnesis.service import MemoryService

service = MemoryService()

service.ingest([...])
service.orient()
service.search("install script")
service.trace_file("scripts/install.sh")
service.trace_decision("curl bootstrap")
service.digest(days=7)
service.sql("SELECT session_id, COUNT(*) AS n FROM events GROUP BY session_id")
```

---

## Data model

### Raw store

#### `sessions`

- `session_id`
- `agent`
- `project_id`
- `started_at`
- `ended_at`
- `metadata_json`

#### `events`

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
- `payload_json`

#### `file_touches`

- `event_id`
- `path`
- `operation`

### UQA sidecar

The current sidecar materializes:

- `sessions`
- `events`
- `file_touches`

The sidecar is rebuildable from the raw store.

---

## Environment variables

### Core

| Variable | Meaning |
|---|---|
| `ANAMNESIS_DB` | raw SQLite database path |
| `ANAMNESIS_UQA_SIDECAR` | UQA sidecar path |
| `UQA_REPO_ROOT` | optional local checkout path for UQA import resolution |
| `ANAMNESIS_LIMIT` | default result limit |

### MCP server

| Variable | Meaning |
|---|---|
| `ANAMNESIS_MCP_TRANSPORT` | `stdio`, `sse`, or `streamable-http` |
| `ANAMNESIS_MCP_HOST` | bind host |
| `ANAMNESIS_MCP_PORT` | bind port |
| `ANAMNESIS_MCP_MOUNT_PATH` | SSE mount path |
| `ANAMNESIS_MCP_SSE_PATH` | SSE endpoint path |
| `ANAMNESIS_MCP_MESSAGE_PATH` | SSE message path |
| `ANAMNESIS_MCP_STREAMABLE_HTTP_PATH` | streamable HTTP path |
| `ANAMNESIS_MCP_LOG_LEVEL` | server log level |
| `ANAMNESIS_MCP_DEBUG` | FastMCP debug flag |
| `ANAMNESIS_MCP_JSON_RESPONSE` | JSON response toggle |
| `ANAMNESIS_MCP_STATELESS_HTTP` | stateless streamable HTTP mode |

---

## Release with uv

### Build both packages

```bash
uv build --all-packages
```

### Verify the release artifacts in a clean uv-managed environment

```bash
uv run python scripts/verify_uv_release.py
```

### Full release helper

```bash
uv run python scripts/release_uv.py
```

### Publish order

Because Anamnesis depends on UQA, publish in this order:

1. `uqa`
2. `anamnesis`

The release helper script is already written around that assumption.

---

## Verification

Current local verification commands:

```bash
uv sync --all-packages --group dev
uv run python -m unittest discover -s tests -v
uv run python -m compileall anamnesis tests scripts
uv run python scripts/verify_uv_release.py
```

---

## Known limitations

- this is **not** a 100% Flex implementation
- current UQA sidecar materialization is intentionally simple
- richer graph/vector materialization is still future work
- some higher-order traces are reconstructed in Python after UQA retrieval rather than from a fully enriched graph schema
- OpenCode export quality depends on upstream export correctness

---

## Bottom line

If you want the most honest one-line description of the project **today**, it is this:

> **Anamnesis is a UQA-native, multi-client agent-memory layer with canonical raw capture, mandatory UQA querying, uv-first packaging, and explicit non-claims about full Flex parity.**
