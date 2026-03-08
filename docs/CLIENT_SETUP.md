# Client setup

## Claude Code

Initialize config only:

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

Generated files:

- `.mcp.json`
- `.claude/settings.local.json`

Configured Claude hooks:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `SessionEnd`

The generated hook command writes into the configured raw database:

- `.anamnesis/anamnesis.db`

Backfill existing Claude history for the current repo:

```bash
uv run anamnesis-claude-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD"
```

Sources currently supported:

- `~/.claude/history.jsonl`
- `~/.claude/projects/<project>/sessions-index.json`
- `~/.claude/transcripts/*.jsonl` (repo-matching transcripts only)

## Codex

Initialize config:

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

Generated files:

- `~/.codex/settings.json`
- `.anamnesis/generated/register-codex-mcp.sh`

Configured Codex hooks:

- `UserPromptSubmit`
- `PostToolUse`

These hooks are now **workspace-routed**:

- the global Codex hook command is not pinned to one repo DB
- each incoming payload uses its own `cwd`
- Anamnesis resolves the nearest workspace root and writes to:
  - `<workspace>/.anamnesis/anamnesis.db`

Register the MCP server if desired:

```bash
bash .anamnesis/generated/register-codex-mcp.sh
```

The generated Codex MCP registration no longer hard-pins `ANAMNESIS_DB`.
It launches the server without a fixed DB env so the Codex-side Anamnesis server can follow the current workspace at process start.

Backfill existing Codex history:

```bash
uv run anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD"
```

Sources currently supported:

- `~/.codex/history.jsonl`
- `~/.codex/sessions/`

## OpenCode

Initialize config:

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

Generated files:

- `.opencode/opencode.json`
- `.opencode/plugins/anamnesis.ts`

Configured OpenCode live events:

- `chat.message`
- `tool.execute.before`
- `tool.execute.after`
- `message.part.updated`
- `file.edited`
- `session.created`
- `session.updated`
- `session.ended`

Backfill existing OpenCode sessions:

```bash
uv run anamnesis-opencode-sync \
  --db .anamnesis/anamnesis.db \
  --workspace-root "$PWD" \
  --all-sessions
```

Import order:

1. try `opencode export`
2. fall back to local OpenCode storage
3. record import failures when recovery is incomplete

## End-to-end smoke test

After `anamnesis-init`, you can verify that all three client adapters still write into the same UQA-backed memory with:

```bash
make smoke-clients
```

This smoke test:

- generates fresh Claude Code / Codex / OpenCode config in a temporary workspace
- ingests one real sample payload through each client adapter
- rebuilds the mandatory UQA sidecar
- verifies `@survey`, `@chronicle`, and free-text search against the shared database

## One-shot bootstrap

If you want config + all historical backfill + UQA rebuild in one command:

```bash
uv run anamnesis-bootstrap --workspace-root "$PWD"
```
