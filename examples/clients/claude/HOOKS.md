# Claude hook wiring

This project now includes a real Claude hook ingestion path:

- hook command: `python3 -m agent_memory.hooks.claude`
- storage target: `.anamnesis/anamnesis.db`
- recommended config location: `.claude/settings.local.json`

The bundled `settings.local.json` example captures:

- `UserPromptSubmit`
- `PreToolUse` with `matcher: "*"`
- `PostToolUse` with `matcher: "*"`
- `SessionEnd`

All of those events are written to:

- `.anamnesis/anamnesis.db` inside `CLAUDE_PROJECT_DIR`

That path matches the `ANAMNESIS_DB` value shown in `examples/clients/claude/.mcp.json`, so the hook writer and MCP server read the same canonical store.

Why `--quiet`?

Claude hook commands can receive JSON on stdin. For this ingestion path we only want to persist events, not emit extra stdout back into the Claude session, so the example uses `--quiet`.
