# CLI reference

## Makefile shortcuts

Common local workflows are wrapped in `make`:

```bash
make help
make install
make init
make test
make build
make verify
make smoke-clients
make codex-sync
make opencode-sync
make mcp
make mcp-http HOST=0.0.0.0 PORT=8000
```

## `anamnesis`

Umbrella CLI for searching, syncing, bootstrapping, and serving MCP.

```bash
uv run anamnesis --help
uv run anamnesis search "@survey"
uv run anamnesis search "@artifact path=src/worker.py"
uv run anamnesis search "@chronicle session=ses-1"
uv run anamnesis search "SELECT COUNT(*) AS n FROM sessions"
uv run anamnesis sync
uv run anamnesis init -- --workspace-root "$PWD"
uv run anamnesis mcp -- --transport streamable-http --host 0.0.0.0 --port 8000
```

Supported subcommands:

- `anamnesis search`
- `anamnesis sync`
- `anamnesis init`
- `anamnesis mcp`

## Anamnesis macros

The public macro vocabulary is:

- `@survey` — schema and coverage overview
- `@synopsis` — recent activity digest
- `@artifact` — file trace / lineage workflow
- `@chronicle` — session narrative reconstruction
- `@cadence` — sprint grouping
- `@lineage` — concept genealogy across sessions/files/tools
- `@crossroads` — shared files or bridge sessions between concepts
- `@relay` — delegation tree / child-session trace
- `@thesis` — decision archaeology
- `@vitals` — health and freshness checks

## `anamnesis-init`

Write deployable client config for Claude Code, Codex, and OpenCode.

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

Useful flags:

- `--workspace-root`
- `--python-executable`
- `--db-path`
- `--clients claude codex opencode`
- `--codex-home`
- `--register-codex`
- `--uqa-repo-root`

## `anamnesis-ingest`

Ingest newline-delimited JSON payloads through one adapter.

```bash
cat payloads.jsonl | uv run anamnesis-ingest --agent claude --db .anamnesis/anamnesis.db
```

## `anamnesis-codex-sync`

Backfill Codex artifacts into the canonical raw store.

```bash
uv run anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

## `anamnesis-opencode-sync`

Backfill OpenCode exported or discovered sessions.

```bash
uv run anamnesis-opencode-sync \
  --db .anamnesis/anamnesis.db \
  --all-sessions
```

## `anamnesis-mcp`

Run the MCP server directly.

```bash
uv run anamnesis-mcp
uv run anamnesis-mcp --transport sse
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## Hook entrypoints

These are mainly used by generated client config:

- `anamnesis-hook-claude`
- `anamnesis-hook-codex`
- `anamnesis-hook-opencode`
