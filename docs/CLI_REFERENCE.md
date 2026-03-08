# CLI reference

## Makefile shortcuts

Common local workflows are wrapped in `make`:

```bash
make help
make install
make init
make bootstrap WORKSPACE_ROOT=~/Desktop/work/pylon
make bootstrap-full WORKSPACE_ROOT=~/Desktop/work/pylon
make bootstrap-fast WORKSPACE_ROOT=~/Desktop/work/pylon
make sidecar WORKSPACE_ROOT=~/Desktop/work/pylon
make test
make build
make verify
make smoke-clients
make claude-sync
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
uv run anamnesis bootstrap -- --workspace-root "$PWD"
uv run anamnesis sync
uv run anamnesis sidecar -- --db .anamnesis/anamnesis.db
uv run anamnesis init -- --workspace-root "$PWD"
uv run anamnesis mcp -- --transport streamable-http --host 0.0.0.0 --port 8000
```

Supported subcommands:

- `anamnesis search`
- `anamnesis bootstrap`
- `anamnesis sync`
- `anamnesis sidecar`
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

## `anamnesis-bootstrap`

Initialize a repo and backfill all matching local Claude/Codex/OpenCode history.

```bash
uv run anamnesis-bootstrap --workspace-root "$PWD" --skip-sidecar-rebuild
```

Repeated runs reuse `.anamnesis/bootstrap-state.json` and skip the historical rescan when the workspace has already been imported.

If you want the full blocking rebuild in one step:

```bash
uv run anamnesis-bootstrap --workspace-root "$PWD"
```

Useful flag when you want ingestion first and indexing later:

- `--skip-sidecar-rebuild`
- `--refresh-backfill`

Example:

```bash
uv run anamnesis-bootstrap --workspace-root "$PWD" --skip-sidecar-rebuild
```

## `anamnesis sidecar`

Rebuild the mandatory UQA sidecar explicitly.

```bash
uv run anamnesis sidecar -- --db .anamnesis/anamnesis.db
```

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

## `anamnesis-claude-sync`

Backfill Claude Code history, project session index, and matching transcripts for a repo.

```bash
uv run anamnesis-claude-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD"
```

## `anamnesis-codex-sync`

Backfill Codex artifacts into the canonical raw store.

```bash
uv run anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD"
```

Live Codex hook capture is workspace-routed automatically; this backfill command is for historical import.

## `anamnesis-opencode-sync`

Backfill OpenCode exported or discovered sessions.

```bash
uv run anamnesis-opencode-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD" \
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
