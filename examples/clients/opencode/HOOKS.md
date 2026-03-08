# OpenCode capture wiring

This project now includes two OpenCode ingestion paths:

1. **Live plugin capture** via `examples/clients/opencode/anamnesis.ts`
2. **Backfill/import** via `python3 -m agent_memory.opencode_sync`

## Live plugin capture

OpenCode loads local plugins from:

- `.opencode/plugins/`
- `~/.config/opencode/plugins/`

To use the example plugin:

1. copy `examples/clients/opencode/anamnesis.ts` to `<project>/.opencode/plugins/anamnesis.ts`
2. make sure `python3 -m agent_memory.hooks.opencode` works from that project directory
3. keep your MCP config pointed at the same database path:
   - `<project>/.anamnesis/anamnesis.db`

The example plugin captures:

- `chat.message`
- `tool.execute.before`
- `tool.execute.after`
- `message.part.updated`
- `file.edited`
- `session.idle`

That gives you:

- user prompts
- tool calls and results
- streamed assistant text deltas
- file touches
- session idle markers

## Backfill prior OpenCode sessions

To import previously saved OpenCode sessions:

```bash
python3 -m agent_memory.opencode_sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

By default that command discovers sessions with:

- `opencode session list`

and exports them with:

- `opencode export <session-id>`

You can also import explicit exports:

```bash
python3 -m agent_memory.opencode_sync \
  --db .anamnesis/anamnesis.db \
  --export-file /path/to/exported-session.json
```

## Important caveat

Some real local OpenCode exports are malformed enough that strict JSON parsing fails.

The importer is best-effort:

- good exports are imported
- parse failures are reported in the summary and skipped

That means the backfill path is usable today, but you should expect occasional skipped sessions until the upstream export format is consistently valid.
