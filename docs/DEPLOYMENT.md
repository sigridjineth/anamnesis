# Deployment guide and review

This document captures the current deployability baseline for **Anamnesis** and the quickest path to ship it as a reusable Python package or MCP-backed service.

## Deployability summary

Current strengths:

- the package builds as a wheel from `pyproject.toml`
- console scripts are published for ingest, Codex sync, OpenCode sync, hooks, and the MCP server
- the core runtime stays stdlib-only
- MCP support is isolated behind the optional `mcp` extra
- runtime paths are centralized in `agent_memory.config.Settings`

Current risks to address before a broader production rollout:

- no container image, process manager unit, or release automation is included yet
- static analysis is not clean yet (`mypy` and `ruff` findings are listed below)
- Flex/UQA integrations still assume local checkouts or explicit env wiring

## Verified install paths

### 1. Build a distributable wheel

```bash
python3 -m pip wheel . -w /tmp/anamnesis-wheelhouse
```

Verified artifact:

- `anamnesis-0.1.0-py3-none-any.whl`

### 2. Install the core package in an isolated environment

```bash
python3 -m venv /tmp/anamnesis-venv
source /tmp/anamnesis-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install /tmp/anamnesis-wheelhouse/anamnesis-0.1.0-py3-none-any.whl
```

### 3. Install MCP support when running the server

Either install from source with extras:

```bash
python3 -m pip install -e '.[mcp]'
```

or add the optional dependency into an existing environment:

```bash
python -m pip install 'mcp>=1.0.0'
```

## Runtime configuration

The runtime is environment-driven, so deployment can stay simple.

| Variable | Use at deploy time |
| --- | --- |
| `ANAMNESIS_DB` | Required when the database should live outside the repo default. Point this at a persistent writable SQLite file. |
| `ANAMNESIS_UQA_SIDECAR` | Optional override for the derived UQA sidecar path. |
| `FLEX_REPO_ROOT` | Optional path to a local read-only Flex checkout. |
| `UQA_REPO_ROOT` | Optional path to a local read-only UQA checkout. |
| `FLEX_CELL` | Optional default Flex cell name. |
| `FLEX_CELL_PATH` | Optional explicit SQLite cell path. |
| `ANAMNESIS_LIMIT` | Optional default result limit for queries. |

Recommended default for deployments:

```bash
export ANAMNESIS_DB=/srv/anamnesis/anamnesis.db
```

That keeps the writable SQLite file on a persistent volume instead of inside the source checkout.

## Smoke-test checklist

After install, these commands should succeed:

```bash
anamnesis-ingest --help
anamnesis-codex-sync --help
anamnesis-opencode-sync --help
```

For MCP-enabled environments, server creation should also succeed after installing the optional dependency:

```bash
python -c 'from agent_memory.mcp_server import create_server; create_server(); print("ok")'
```

## End-to-end deployment smoke test

This verifies the packaged CLI can ingest an event into a fresh SQLite file and query it back through `MemoryService`:

```bash
TMPDIR=$(mktemp -d)
DB="$TMPDIR/anamnesis.db"
cat > "$TMPDIR/payload.json" <<'JSON'
{"event":"UserPromptSubmit","session_id":"deploy-smoke","project":"deploy-smoke","timestamp":"2026-03-08T00:00:00Z","prompt":"hello deploy"}
JSON

anamnesis-ingest --agent claude --db "$DB" --input "$TMPDIR/payload.json"
ANAMNESIS_DB="$DB" python - <<'PY'
from agent_memory.service import MemoryService
service = MemoryService()
print(service.orient(project_id='deploy-smoke')["counts"]["events"])
print(len(service.search('hello', project_id='deploy-smoke')["results"]))
PY
```

Expected result:

- one event is ingested
- one search hit is returned for `hello`

## Operational notes

- The canonical store auto-initializes on first use, so there is no separate migration step today.
- The MCP server will fail fast with an install hint if the optional `mcp` dependency is missing.
- SQLite file permissions matter in deployment; the runtime user needs write access to `ANAMNESIS_DB` and its parent directory.
- Flex and UQA should be treated as optional read-only integrations, not hard runtime dependencies.

## Review findings

### Ready now

- packaging metadata is present in `pyproject.toml`
- wheel build succeeds
- console entry points are exported for the main workflows
- isolated wheel install succeeds
- MCP server creation succeeds once the optional dependency is installed

### Follow-up recommended before wider rollout

#### Static typing baseline

`mypy agent_memory` currently reports failures, including:

- missing optional import stubs for `flex.registry` and `uqa.engine`
- several `OpenCodeAdapter` typing mismatches
- `MemoryService` calls that pass `Path | None` into constructors expecting concrete paths
- `IngestionService` adapter instantiation typing issues

#### Lint baseline

`ruff check agent_memory tests` currently reports fixable issues, including:

- unused imports in `agent_memory/backends/uqa.py`, `agent_memory/contracts.py`, `agent_memory/query.py`, `agent_memory/sync/flex_to_uqa.py`, and `tests/test_service.py`
- unnecessary `f` prefixes in `agent_memory/providers/flex.py`

## Release checklist

Before calling this production-ready, complete the following:

1. clean up the current `mypy` failures or document an intentional type-check policy
2. clean up the current `ruff` findings
3. define the supported deployment shape (wheel only, container image, or both)
4. document persistent volume and backup expectations for the SQLite database
5. add process supervision guidance for the MCP server if it will run continuously
