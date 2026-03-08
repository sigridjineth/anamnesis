# Quickstart

## 1. Clone and install

```bash
git clone https://github.com/sigridjineth/anamnesis.git
cd anamnesis
make install
```

Equivalent manual `uv` flow:

```bash
git submodule update --init --recursive
uv sync --all-packages --group dev
```

## 2. Initialize client config

```bash
make init
```

This writes deployable local config for:

- Claude Code
- Codex
- OpenCode

Quick sanity check for all three client integrations:

```bash
make smoke-clients
```

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
print(service.survey())
print(service.search("install script"))
print(service.thesis("curl install script"))
```

## 5. Query through MCP

```bash
make mcp
```

Or HTTP:

```bash
make mcp-http HOST=0.0.0.0 PORT=8000
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

- memory survey
- hybrid search
- artifact history and related files
- thesis / chronicle tracing
- cadence grouping
- lineage / crossroads / relay views
- read-only SQL against the UQA sidecar
