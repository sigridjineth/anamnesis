# Client setup

## Claude Code

Initialize config:

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

The generated hook command writes into the project-local raw database:

- `.anamnesis/anamnesis.db`

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

Register the MCP server if desired:

```bash
bash .anamnesis/generated/register-codex-mcp.sh
```

Backfill existing Codex history:

```bash
uv run anamnesis-codex-sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
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
  --all-sessions
```

Import order:

1. try `opencode export`
2. fall back to local OpenCode storage
3. record import failures when recovery is incomplete
