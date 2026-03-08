# uv release workflow

This repository uses a uv-first release workflow.

Because Anamnesis depends on UQA, release order matters.

## Build both distributions

```bash
uv build --all-packages
```

That produces artifacts for:

- `uqa`
- `anamnesis`

## Clean-environment verification

```bash
uv run python scripts/verify_uv_release.py
```

That script:

1. builds both workspace packages with `uv build --all-packages`
2. creates a clean uv-managed virtualenv
3. installs the built `uqa` wheel
4. installs the built `anamnesis` wheel
5. smoke-tests the packaged CLIs
6. runs an ingest smoke test

Optional MCP verification:

```bash
uv run python scripts/verify_uv_release.py --with-mcp
```

## Full helper

```bash
uv run python scripts/release_uv.py
```

By default it:

- builds
- verifies
- stops before publish

## Publish

When you are ready:

```bash
export UV_PUBLISH_TOKEN=...
uv run python scripts/release_uv.py --publish --check-url https://pypi.org/simple/
```

The helper publishes in dependency order:

1. `uqa`
2. `anamnesis`

## Practical note

If you clone without submodules, initialize `uqa/` before running the release workflow:

```bash
git submodule update --init --recursive
```
