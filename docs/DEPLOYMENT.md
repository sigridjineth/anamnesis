# Deployment notes

## Local developer setup

```bash
git submodule update --init --recursive
uv sync --all-packages --group dev
```

## Run the MCP server

```bash
uv run anamnesis-mcp
uv run anamnesis-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## Generate client config

```bash
uv run anamnesis-init --workspace-root "$PWD"
```

## Important deployment assumptions

- UQA is mandatory
- the sidecar is the supported query layer
- the raw SQLite database is a capture store, not the final query engine
- `uqa/` is kept as a submodule for local workspace/release workflows

## Environment variables

### Core

- `ANAMNESIS_DB`
- `ANAMNESIS_UQA_SIDECAR`
- `UQA_REPO_ROOT`
- `ANAMNESIS_LIMIT`

### MCP

- `ANAMNESIS_MCP_TRANSPORT`
- `ANAMNESIS_MCP_HOST`
- `ANAMNESIS_MCP_PORT`
- `ANAMNESIS_MCP_MOUNT_PATH`
- `ANAMNESIS_MCP_SSE_PATH`
- `ANAMNESIS_MCP_MESSAGE_PATH`
- `ANAMNESIS_MCP_STREAMABLE_HTTP_PATH`
- `ANAMNESIS_MCP_LOG_LEVEL`
- `ANAMNESIS_MCP_DEBUG`
- `ANAMNESIS_MCP_JSON_RESPONSE`
- `ANAMNESIS_MCP_STATELESS_HTTP`

## Release preconditions

Before publishing Anamnesis:

1. build and publish `uqa`
2. verify Anamnesis against locally built `uqa` artifacts
3. publish Anamnesis

The workspace and release scripts are already arranged for that order.
