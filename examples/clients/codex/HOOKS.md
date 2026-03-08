# Codex capture wiring

This project now includes two Codex ingestion paths:

1. **Live hook capture** via `python3 -m agent_memory.hooks.codex`
2. **Backfill/import** via `python3 -m agent_memory.codex_sync`

## Live hooks

The bundled `settings.json` example is shaped like a real local Codex `~/.codex/settings.json` hook config.

It captures:

- `UserPromptSubmit`
- `PostToolUse`

Why only those two?

- `UserPromptSubmit` captures the prompt directly.
- `PostToolUse` is enough for the canonical store because the Codex adapter expands that payload into both a `tool_call` and a `tool_result` event.

Replace `/ABSOLUTE/PATH/TO/uqa-vibe` in `settings.json`, then merge the relevant entries into your local `~/.codex/settings.json`.

## Backfill prior Codex history

To import prior Codex prompts and transcript items into the same canonical store:

```bash
python3 -m agent_memory.codex_sync \
  --db .anamnesis/anamnesis.db \
  --project-id "$PWD"
```

By default that command reads:

- `~/.codex/history.jsonl`
- `~/.codex/sessions/`

and imports:

- prompt history lines
- assistant messages from transcript files
- `function_call` items as `tool_call`
- `function_call_output` items as `tool_result`

Transcript user messages are skipped by default so they do not duplicate `history.jsonl` prompts. Add `--include-user-messages` if you explicitly want them.

## Important note on project scoping

Codex history and transcript files do not reliably carry a repository/project identifier.

When you want repo-scoped queries later, pass `--project-id "$PWD"` during backfill so the imported events are tagged to the current workspace.
