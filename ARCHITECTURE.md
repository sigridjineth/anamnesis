# Anamnesis architecture

## Core idea

Anamnesis separates:

1. **capture**
2. **canonical storage**
3. **query execution**

Capture is client-specific.
Storage is normalized.
Query execution is UQA-only.

---

## Pipeline

```text
client hooks / exports
    -> adapter normalization
    -> canonical raw SQLite
    -> UQA sidecar rebuild
    -> MCP + Python API + Anamnesis CLI
```

---

## Layers

### Adapters

Client-specific adapters normalize raw payloads into `CanonicalEvent`.

Supported clients:

- Claude Code
- Codex
- OpenCode

### Raw store

The raw store is the durable append surface.

Tables:

- `sessions`
- `events`
- `file_touches`
- `import_failures`

### UQA sidecar

The sidecar is rebuilt from the raw store and is the supported query engine.

It materializes:

- `projects`
- `sessions`
- `files`
- `events`
- `touch_activity`
- `search_docs`
- `graph_edges`
- persisted UQA graph vertices / edges
- persisted UQA vectors for hybrid retrieval

### Service layer

`anamnesis.service.MemoryService` is the application facade used by:

- CLI tools
- MCP tools
- Python callers

---

## Why UQA is mandatory

Current contract:

- raw storage may exist without an already-built sidecar
- query execution may not proceed without UQA availability
- if the sidecar is stale or missing, Anamnesis rebuilds it
- if UQA is unavailable, that is an error state

---

## Design choices

Anamnesis intentionally:

- centers UQA sidecar projection as the query core
- keeps the raw store simple and rebuildable
- favors local query ergonomics over service-heavy packaging
