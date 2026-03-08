# OpenCode capture wiring

This project now includes two OpenCode ingestion paths:

1. **Live plugin capture** via `examples/clients/opencode/anamnesis.ts`
2. **Backfill/import** via `python3 -m anamnesis.opencode_sync`

## Live plugin capture

OpenCode loads local plugins from:

- `.opencode/plugins/`
- `~/.config/opencode/plugins/`

To use the example plugin:

1. copy `examples/clients/opencode/anamnesis.ts` to `<project>/.opencode/plugins/anamnesis.ts`
2. make sure `python3 -m anamnesis.hooks.opencode` works from that project directory
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
python3 -m anamnesis.opencode_sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

By default that command discovers sessions with:

- `opencode session list`
- and, if that is unavailable, local OpenCode storage discovery

It then prefers:

- `opencode export <session-id>`
- and falls back to reconstructing the session from local OpenCode storage when export fails

You can also import explicit exports:

```bash
python3 -m anamnesis.opencode_sync \
  --db .anamnesis/anamnesis.db \
  --export-file /path/to/exported-session.json
```

## Import robustness

The importer now has two recovery layers:

- tolerant export parsing for noisy / prefixed `opencode export` output
- local storage fallback assembled from OpenCode's `storage/session`, `storage/message`, and `storage/part` artifacts

So the normal order is:

1. try `opencode export <session-id>`
2. if export fails or discovery is unavailable, reconstruct from local storage
3. only report a failure when neither path succeeds
