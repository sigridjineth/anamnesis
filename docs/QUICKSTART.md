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

## 2. First-time bootstrap for an existing repo

If you want to stand inside another repo (for example `~/Desktop/work/pylon`) and do **everything in one go**:

```bash
cd ~/Desktop/work/pylon
/Users/sigridjineth/Desktop/work/uqa-vibe/.venv/bin/python \
  /Users/sigridjineth/Desktop/work/uqa-vibe/scripts/bootstrap_workspace_memory.py
```

That one command will:

- generate Claude / Codex / OpenCode config for the repo
- register the Codex MCP entry (unless you pass `--skip-register-codex`)
- backfill matching Claude history / transcripts / project index
- backfill matching Codex history / sessions
- backfill matching OpenCode sessions
- leave sidecar rebuild for an explicit follow-up step so bootstrap returns quickly

If you are already inside the Anamnesis repo, the equivalent shortcut is:

```bash
make bootstrap WORKSPACE_ROOT=~/Desktop/work/pylon
```

If you run `make bootstrap` again for the same workspace, Anamnesis now reuses the recorded bootstrap state and skips the expensive historical rescan unless you explicitly refresh it.

Then rebuild the mandatory UQA sidecar explicitly when you want full query coverage:

```bash
make sidecar WORKSPACE_ROOT=~/Desktop/work/pylon
```

If you really want the full blocking rebuild in one step:

```bash
make bootstrap-full WORKSPACE_ROOT=~/Desktop/work/pylon
```

To force a full historical rescan for the workspace:

```bash
uv run anamnesis-bootstrap --workspace-root ~/Desktop/work/pylon --skip-sidecar-rebuild --refresh-backfill
```

## 3. Initialize client config only

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

## 4. Ingest data manually

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

## 5. Query through Python

```python
from anamnesis.service import MemoryService

service = MemoryService()
print(service.survey())
print(service.search("install script"))
print(service.thesis("curl install script"))
```

## 6. Query through MCP

```bash
make mcp
```

Or HTTP:

```bash
make mcp-http HOST=0.0.0.0 PORT=8000
```

## 7. Rebuild the UQA sidecar explicitly

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
