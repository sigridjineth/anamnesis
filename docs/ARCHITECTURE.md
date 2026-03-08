# Detailed architecture

## Overview

Anamnesis uses a two-stage persistence model:

1. canonical raw SQLite capture
2. mandatory UQA sidecar projection

This keeps ingestion simple while ensuring the query surface is UQA-native.

---

## Stage 1: raw capture

Raw capture is append-oriented and intentionally boring.

Why:

- hooks should not need to understand UQA internals
- debugging ingestion should be possible with stock SQLite tools
- rebuilding derived query state should stay cheap and deterministic

The raw schema is:

- `sessions`
- `events`
- `file_touches`
- `import_failures`

---

## Stage 2: UQA projection

The UQA sidecar is rebuilt from the raw store.

Current projection is intentionally richer and includes:

- `projects`
- `sessions`
- `files`
- `events`
- `touch_activity`
- `search_docs`
- `graph_edges`
- persisted UQA graph vertices / edges
- persisted UQA vectors for hybrid retrieval

That supports:

- UQA hybrid lexical + vector search
- sidecar-backed orientation
- read-only SQL against the projected memory
- digest / trace / story / genealogy helpers over UQA-readable tables
- graph-aware session/file/event navigation

---

## Query contract

All supported query paths go through UQA.

That includes:

- `MemoryService.search`
- `MemoryService.orient`
- `MemoryService.trace_file`
- `MemoryService.trace_decision`
- `MemoryService.digest`
- `MemoryService.sql`

There is no supported Flex or generic-SQLite fallback path anymore.

---

## Client adapters

### Claude

Handles hook payloads from Claude Code lifecycle events.

### Codex

Handles:

- prompt hooks
- tool-use hooks
- `history.jsonl`
- transcript/session JSON

### OpenCode

Handles:

- live plugin events
- exported sessions from `opencode export`
- local storage fallback reconstructed from OpenCode storage artifacts when export is unavailable or malformed
- persistent import-failure recording for later inspection and health checks

---

## Packaging model

The root project is an **uv package**.

The repository also carries `uqa/` as:

- a git submodule
- a uv workspace member

That enables a local release workflow that builds and verifies:

- `uqa`
- `anamnesis`

in one workspace.
