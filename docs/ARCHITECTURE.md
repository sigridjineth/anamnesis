# Architecture

## Goal

Expose one query interface that Claude, Codex, and OpenCode can all consume through MCP, while keeping ingestion product-specific.

## Layering

### 1. Capture adapters (product-specific)

Each agent environment emits different raw events:

- Claude Code: hooks / tool lifecycle / session metadata
- Codex: hooks, `history.jsonl`, and session transcript files
- OpenCode: plugins, `opencode export` session dumps, and session/tool events

These should normalize into a canonical event model instead of sharing raw formats.

### 2. Raw memory substrate (append-only)

Store normalized events in SQLite-first cells/tables. Keep this layer durable and minimally transformed.

### 3. Query/index layer

Use:

- **Flex-style SQLite cells** for portable, inspectable storage
- **UQA** for richer SQL + text + vector + graph querying over indexed views/sidecars

### 4. Shared interface

Expose a small MCP surface:

- `memory_orient`
- `memory_search`
- `memory_trace_file`
- `memory_trace_decision`
- `memory_digest`
- `memory_sql`
- `memory_health`

## Why not one giant plugin abstraction?

Because plugin/hook models differ across clients. The stable cross-client contract is:

- **MCP** for querying
- **shared skills/workflows** for UX
- **adapter-specific collectors** for ingestion

## Initial implementation choices

- Root package: `agent_memory`
- Backends:
  - `agent_memory.backends.flex` — SQLite/cell-first querying
  - `agent_memory.backends.uqa` — UQA Engine-based querying
- Coordinator:
  - `agent_memory.service.MemoryService`
- Transport:
  - `agent_memory.mcp_server`
- Bootstrap:
  - `agent_memory.init_cli`

## Sidecar direction

Recommended long-term layout:

- raw cell: `claude_code.db`
- query/index sidecar: `claude_code.uqa.db`

That lets Flex-style schemas stay portable while UQA owns richer indexing, graph edges, and query planning.

## Deploy from the package, not from the examples

The `examples/clients/` directory remains useful as documentation, but actual deployment now comes from `anamnesis-init`.
That keeps the runtime simple:

1. install the Python package
2. run `anamnesis-init`
3. point Claude/Codex/OpenCode at the generated config
