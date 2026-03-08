# Anamnesis

**Anamnesis** is a **UQA-native shared memory layer** for **Claude Code**, **Codex**, and **OpenCode**.

It captures agent activity into a canonical SQLite raw store, materializes a mandatory **UQA** sidecar, and serves one shared query surface across clients.

> Core stance: **UQA is required**. There is no supported non-UQA query mode.

## What it gives you

- shared search across Claude Code, Codex, and OpenCode history
- repo-scoped/project-scoped memory separation
- file history, lineage, story, genealogy, delegation, and digest workflows
- Flex-compatible query entrypoints such as `flex search`, `@orient`, `@file`, and `@story`
- MCP + Python access to the same UQA-backed memory surface

## Architecture

```text
Claude hooks   ─┐
Codex hooks    ─┼─> adapters -> raw SQLite -> UQA sidecar -> MCP / Python / flex facade
OpenCode hooks ─┘
```

## Install with uv

```bash
git clone https://github.com/sigridjineth/anamnesis.git
cd anamnesis
git submodule update --init --recursive
uv sync --all-packages --group dev
```

## Quickstart

Initialize local client config:

```bash
uv run anamnesis-init --workspace-root "$PWD"
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

Or run the MCP server:

```bash
uv run anamnesis-mcp
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

Or use the Flex-compatible CLI surface:

```bash
uv run flex search "@orient"
uv run flex search "@file path=src/worker.py"
uv run flex search "@story session=ses-1"
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
- [Flex parity and scope](docs/FLEX_PARITY.md)

## Verification

Recent local verification:

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall anamnesis tests scripts
uv build --all-packages
uv run python scripts/verify_uv_release.py
```

## Bottom line

If you want one **UQA-backed agent memory/query surface** across Claude Code, Codex, and OpenCode, that is what Anamnesis is building.
