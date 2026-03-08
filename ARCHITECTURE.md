# Architecture

## Principle

Split the problem into three layers:

1. **Capture adapters** — product-specific ingestion for Claude, Codex, OpenCode
2. **Shared query layer** — one Python/MCP interface for schema discovery and memory queries
3. **Optional UQA sidecar** — richer indexing/search over normalized records projected from a Flex cell

## Why this shape

Claude, Codex, and OpenCode do not share the same hook/plugin model.
They *do* all support MCP-style tool access, so the natural common layer is:

- client-specific adapters on the way in
- MCP on the way out

## Current scaffold

```
agent_memory/
  adapters/      Canonical event contracts + starter normalizers
  hooks/         Thin stdin hook entrypoints for Claude/Codex/OpenCode
  codex_sync.py  Codex history + transcript backfill/import
  opencode_sync.py OpenCode export/session backfill/import
  providers/     Flex raw-cell provider + UQA sidecar provider
  sync/          Flex -> UQA projection
  service.py     Shared interface entry point
  mcp_server.py  MCP wrapper (stdio plus deployable HTTP transports)
examples/clients/
  claude/
  codex/
  opencode/
```

## Deployable bootstrap layer

`agent_memory.init_cli` turns the source layout into an installable workflow:

- Claude:
  - writes `.mcp.json`
  - writes `.claude/settings.local.json`
- Codex:
  - merges hook entries into `~/.codex/settings.json`
  - emits a ready-to-run `codex mcp add ...` script
  - can optionally register the MCP server directly
- OpenCode:
  - writes `.opencode/opencode.json`
  - writes `.opencode/plugins/anamnesis.ts`

This is the layer that makes the repo deployable as a Python package instead of only a source checkout.

## Query flow

### memory_sql
Runs read-only SQL directly against the Flex cell.

### memory_search
1. Resolve Flex cell
2. If UQA is importable, ensure the sidecar is fresh
3. Run `text_match(content, ...)` on the UQA `memory_events` table
4. Fall back to Flex `LIKE` search when UQA is unavailable

### memory_trace_file
Uses the Flex cell directly because file lineage is naturally preserved in the source schema.

## Canonical record projection

The Flex -> UQA projection currently targets one denormalized table:

- `memory_events(id, session_id, project, created_at, type, role, tool_name, path, content)`

That keeps the first integration simple while leaving room for later graph/vector enrichment.

## Next steps

1. Add richer OpenCode live-capture coverage beyond the current example plugin if the upstream event surfaces stabilize further
2. Materialize graph edges into the UQA sidecar
3. Add embeddings for real `knn_match()` support
4. Add shared skills packaging for Claude/Codex/OpenCode
5. Add richer presets that compile to SQL/UQA automatically
