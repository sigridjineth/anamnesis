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
    -> MCP + Python API
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

### UQA sidecar

The sidecar is rebuilt from the raw store and is the supported query engine.

It currently materializes:

- `sessions`
- `events`
- `file_touches`

### Service layer

`anamnesis.service.MemoryService` is the application facade used by:

- CLI tools
- MCP tools
- Python callers

---

## Why UQA is mandatory

The repo used to tolerate fallback-oriented wording.
That is no longer the intended contract.

Current contract:

- raw storage may exist without an already-built sidecar
- query execution may not proceed without UQA availability
- if the sidecar is stale or missing, Anamnesis rebuilds it
- if UQA is unavailable, that is an error state

---

## What this architecture does not claim

It does not claim:

- full Flex compile/enrichment parity
- a complete background worker/service platform like Flex
- a fully enriched graph schema yet
- production-complete semantic/vector parity with the broader Flex site surface
