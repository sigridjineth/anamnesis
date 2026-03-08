# CLI reference

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

Run the MCP server.

```bash
uv run anamnesis-mcp
uv run anamnesis-mcp --transport sse
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## `flex`

Flex-compatible CLI facade over the Anamnesis/UQA query surface.

```bash
uv run flex search "@orient"
uv run flex search "@digest days=7"
uv run flex search "@file path=src/worker.py"
uv run flex search "@story session=ses-1"
uv run flex search "SELECT COUNT(*) AS n FROM sessions"
```

Supported subcommands:

- `flex search`
- `flex init`
- `flex mcp`

## Hook entrypoints

These are mainly used by generated client config:

- `anamnesis-hook-claude`
- `anamnesis-hook-codex`
- `anamnesis-hook-opencode`
