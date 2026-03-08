# Flex parity and scope

## Short answer

**Anamnesis now covers the main end-user memory features people usually mean when they reference Flex, but it is still not a byte-for-byte reimplementation of Flex's entire runtime/product surface.**

That distinction matters:

- **user-facing memory workflows:** mostly covered
- **Flex's exact operational shape:** not reproduced exactly

## What is covered now

The current Anamnesis/UQA stack supports the high-value features that matter for agent memory and retrieval:

- **shared session search** across Claude Code, Codex, and OpenCode
- **repo-scoped/project-scoped separation**
- **weekly/daily digest style summaries**
- **file lineage**
  - touches across sessions
  - rename/move/copy hint extraction
  - stable file identity per repo
- **decision archaeology**
  - query a concept
  - recover the sessions where it was discussed/worked on
- **session story views**
  - ordered event timeline
  - touched files
- **work sprint detection**
  - sessions grouped by inactivity gap
- **genealogy / concept trail**
  - concept-oriented timeline across events, files, sessions, and tool runs
- **delegation trees / sub-agent traces**
  - parent/child session links
  - nested delegation walk
- **semantic + lexical hybrid retrieval**
  - UQA text/vector fusion on the sidecar
- **local-first deployability**
  - SQLite raw store
  - UQA sidecar
  - uv build/release workflow
  - MCP server
- **Flex-compatible CLI/MCP query surface**
  - `flex search "..."`
  - `@orient`, `@digest`, `@file`, `@story`, `@sprints`, `@genealogy`, `@health`
  - MCP `flex_search`

## Concrete parity notes

### File change / file lineage tracking

Implemented:

- file touches are materialized into `touch_activity`
- file aliases are materialized into `file_aliases`
- rename/move/copy hints are materialized into `file_lineage`
- `trace_file(...)` now returns:
  - matching files
  - aliases
  - lineage edges
  - touch timeline
  - related files

### Repo-by-repo separation

Implemented:

- project filtering is propagated through:
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
- sprint reconstruction is now project-aware
- session aggregation inside the sidecar is now keyed with repo scope instead of assuming one global session namespace

### Delegation / sub-agent traces

Implemented:

- session link extraction from payloads
- materialized `session_links`
- materialized `tool_runs`
- `delegation_tree(...)` for root / ancestor / descendant traversal

## What is still intentionally different from Flex

Anamnesis still does **not** try to exactly clone:

- Flex's exact `_raw_*`, `_edges_*`, `_types_*`, `_enrich_*` table naming ecosystem
- Flex's daemon/worker/service packaging model
- Flex-specific relay/cloud/runtime packaging
- Flex's exact `vec_ops(...)` token DSL and graph community/hub enrichment model

So the honest statement is:

> **Anamnesis now has strong functional parity for the core memory workflows, but not exact product/runtime parity.**

## Why Anamnesis is intentionally different

Anamnesis is built around a stricter query stance:

- **UQA is mandatory**
- the query layer is **always UQA**
- there is **no non-UQA fallback**

The supported mental model is:

1. capture raw events
2. rebuild the UQA sidecar
3. query everything through UQA

## Verification evidence

Recent local verification:

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall anamnesis tests scripts
uv build --all-packages
uv run python scripts/verify_uv_release.py
```

Notable regression coverage includes:

- repo-scoped query separation
- file copy lineage visibility
- nested delegation tree traversal
- project-aware MCP forwarding
- deployability / wheel smoke verification
