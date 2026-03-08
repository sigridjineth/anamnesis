# Quickstart

## 1. Clone and sync

```bash
git clone https://github.com/sigridjineth/anamnesis.git
cd anamnesis
git submodule update --init --recursive
uv sync --all-packages --group dev
```

## 2. Initialize client config

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

This writes deployable local config for:

- Claude Code
- Codex
- OpenCode

## 3. Ingest data

### Claude Code hook payload

```bash
printf '%s\n' '{"event":"UserPromptSubmit","session_id":"s1","project":"demo","timestamp":"2026-03-08T00:00:00Z","prompt":"find install script history"}' \
  | uv run anamnesis-hook-claude --db .anamnesis/anamnesis.db
```

### Codex backfill

```bash
uv run anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

### OpenCode backfill

```bash
uv run anamnesis-opencode-sync \
  --db .anamnesis/anamnesis.db \
  --all-sessions
```

## 4. Query through Python

```python
from anamnesis.service import MemoryService

service = MemoryService()
print(service.orient())
print(service.search("install script"))
print(service.trace_decision("curl install script"))
```

## 5. Query through MCP

```bash
uv run anamnesis-mcp
```

Or HTTP:

```bash
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## 6. Rebuild the UQA sidecar explicitly

```bash
uv run python - <<'PY'
from anamnesis.service import MemoryService
print(MemoryService().rebuild_uqa_sidecar())
PY
```

## What you get

After ingest + rebuild, Anamnesis gives you a UQA-backed surface for:

- memory orientation
- hybrid search
- file history and related files
- decision/session tracing
- session story views
- sprint grouping
- genealogy / bridge / delegation views
- read-only SQL against the UQA sidecar
