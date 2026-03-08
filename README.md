# Anamnesis

**Anamnesis** is a **UQA-native shared memory layer** for **Claude Code**, **Codex**, and **OpenCode**.

It captures agent activity into a canonical SQLite raw store, rebuilds a mandatory **UQA** sidecar, and exposes one shared query surface across clients.

> Core stance: **UQA is required.** There is no supported non-UQA query mode.

## What it gives you

- shared search across Claude Code, Codex, and OpenCode history
- repo-scoped/project-scoped memory separation
- file history, lineage, chronology, delegation, and digest workflows
- one MCP + Python + CLI surface over the same UQA-backed memory
- Anamnesis macros such as `@survey`, `@artifact`, `@chronicle`, and `@synopsis`

## Architecture

```text
Claude hooks   ─┐
Codex hooks    ─┼─> adapters -> raw SQLite -> UQA sidecar -> MCP / Python / Anamnesis CLI
OpenCode hooks ─┘
```

## Install

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

## Quickstart

First-time bootstrap for a repo (config + Claude/Codex/OpenCode backfill, without blocking on a full UQA rebuild):

```bash
cd ~/Desktop/work/pylon
/Users/sigridjineth/Desktop/work/uqa-vibe/.venv/bin/python \
  /Users/sigridjineth/Desktop/work/uqa-vibe/scripts/bootstrap_workspace_memory.py
```

Or, from inside this repo:

```bash
make bootstrap WORKSPACE_ROOT=~/Desktop/work/pylon
make sidecar WORKSPACE_ROOT=~/Desktop/work/pylon
```

Repeated `make bootstrap` runs are cheap: once a workspace has already been backfilled, Anamnesis records that state and skips the historical rescan unless you explicitly request a refresh.

If you really want the full blocking rebuild in one step:

```bash
make bootstrap-full WORKSPACE_ROOT=~/Desktop/work/pylon
```

Initialize local client config only:

```bash
make init
```

Ingest a sample Claude payload:

```bash
printf '%s\n' '{"event":"UserPromptSubmit","session_id":"s1","project":"demo","timestamp":"2026-03-08T00:00:00Z","prompt":"find install script history"}' \
  | uv run anamnesis-hook-claude --db .anamnesis/anamnesis.db
```

Query through Python:

```python
from anamnesis.service import MemoryService

service = MemoryService()
print(service.search("install script"))
```

Run the MCP server:

```bash
make mcp
make mcp-http HOST=0.0.0.0 PORT=8000
```

Query through the umbrella CLI:

```bash
uv run anamnesis search "@survey"
uv run anamnesis search "@artifact path=src/worker.py"
uv run anamnesis search "@chronicle session=ses-1"
uv run anamnesis search "@synopsis days=7"
```

Smoke-test all three client integrations end-to-end:

```bash
make smoke-clients
```

More: [Quickstart](docs/QUICKSTART.md)

## Documentation

Start here:

- [Documentation index](docs/README.md)
- [Quickstart](docs/QUICKSTART.md)
- [Client setup](docs/CLIENT_SETUP.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data model](docs/DATA_MODEL.md)
- [Deployment](docs/DEPLOYMENT.md)
- [uv release workflow](docs/RELEASE_UV.md)
- [PyPI publish checklist](docs/PYPI_PUBLISH_CHECKLIST.md)
- [Feature coverage](docs/FEATURE_COVERAGE.md)

## Verification

Recent local verification:

```bash
make test
make build
make verify
make smoke-clients
```

Equivalent manual `uv` flow:

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall anamnesis tests scripts
uv build --all-packages
uv run python scripts/verify_uv_release.py
```

## Bottom line

If you want one **UQA-backed agent memory/query surface** across Claude Code, Codex, and OpenCode, that is what Anamnesis provides.
