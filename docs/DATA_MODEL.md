# Data model

## Raw store

The raw SQLite store is the canonical capture layer.

Tables:

- `sessions`
- `events`
- `file_touches`
- `import_failures`

### `sessions`

Columns include:

- `session_id`
- `agent`
- `project_id`
- `started_at`
- `ended_at`
- `metadata_json`

### `events`

Columns include:

- `id`
- `agent`
- `session_id`
- `project_id`
- `ts`
- `kind`
- `role`
- `content`
- `tool_name`
- `target_path`
- `payload_json`

### `file_touches`

Columns include:

- `event_id`
- `path`
- `operation`

### `import_failures`

Used to preserve ingestion/export recovery failures for later inspection.

Columns include:

- `agent`
- `source`
- `ref`
- `ts`
- `error`
- `raw_excerpt`

## Mandatory UQA sidecar

The sidecar is rebuilt from the raw store and is the only supported query backend.

Current materialization includes:

- `projects`
- `sessions`
- `files`
- `file_aliases`
- `file_lineage`
- `events`
- `tool_runs`
- `session_links`
- `touch_activity`
- `search_docs`
- `graph_edges`
- persisted UQA graph vertices / edges
- persisted UQA vectors

## Why both layers exist

The raw store exists because hooks should append into something simple and durable.

The UQA sidecar exists because querying, ranking, graph traversal, and read-only SQL should all go through the same UQA-native surface.
