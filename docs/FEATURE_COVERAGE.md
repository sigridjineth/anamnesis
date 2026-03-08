# Feature coverage

## Short answer

**Anamnesis covers the full intended shared-memory workflow surface for this repository: capture, repo-scoped storage, file/session tracing, delegation tracing, hybrid retrieval, MCP access, and deployable uv packaging.**

## Covered workflows

The current Anamnesis/UQA stack supports:

- shared session search across Claude Code, Codex, and OpenCode
- repo-scoped and project-scoped separation
- file lineage
  - touches across sessions
  - rename / move / copy hint extraction
  - stable file identity per repo
- decision archaeology
- session chronicle views
- sprint grouping by inactivity gaps
- concept lineage across events, files, sessions, and tool runs
- delegation trees and child-session traces
- lexical + vector + graph-aware retrieval via the UQA sidecar
- local-first deployment
  - SQLite raw store
  - mandatory UQA sidecar
  - uv build / release workflow
  - MCP server
  - umbrella CLI

## Public query surface

The supported user-facing query surface is:

- `anamnesis search "..."`
- macros: `@survey`, `@synopsis`, `@artifact`, `@chronicle`, `@cadence`, `@lineage`, `@crossroads`, `@relay`, `@thesis`, `@vitals`
- MCP `anamnesis_search`

Legacy runtime-era macro names are intentionally rejected with a replacement hint so callers migrate onto the Anamnesis vocabulary.

## Concrete capability notes

### File change and lineage tracking

Implemented:

- file touches materialized into `touch_activity`
- file aliases materialized into `file_aliases`
- rename / move / copy hints materialized into `file_lineage`
- `trace_file(...)` returns:
  - matching files
  - aliases
  - lineage edges
  - touch timeline
  - related files

### Repo-by-repo separation

Implemented:

- project filtering propagates through:
  - `search`
  - `trace_file`
  - `trace_decision`
  - `digest`
  - `story`
  - `sprints`
  - `genealogy`
  - `bridges`
  - `delegation_tree`
- same paths in different repos remain separated at query time
- sprint reconstruction is project-aware
- session aggregation inside the sidecar is keyed with repo scope

### Delegation and sub-agent traces

Implemented:

- session link extraction from payloads
- materialized `session_links`
- materialized `tool_runs`
- `delegation_tree(...)` for root / ancestor / descendant traversal

## Query stance

Anamnesis is intentionally strict:

1. capture raw events
2. rebuild the UQA sidecar
3. query everything through UQA

There is no supported non-UQA query mode.

## Verification evidence

Recent local verification:

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall anamnesis tests scripts
uv build --all-packages
uv run python scripts/verify_uv_release.py
```
