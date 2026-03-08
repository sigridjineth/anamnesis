# PyPI publish checklist

Use this checklist right before publishing `uqa` and `anamnesis` with `uv`.

## Ground rules

- `uqa` ships first
- `anamnesis` ships second
- both artifacts must be built with `uv`
- Anamnesis must verify cleanly against wheel-installed `uqa`

## 1. Make sure the tree is releasable

```bash
git status --short
make test
make smoke-clients
make build
make verify
```

Expected result:

- working tree is clean
- unit tests pass
- Claude Code / Codex / OpenCode smoke test passes
- `uv build --all-packages` succeeds
- `scripts/verify_uv_release.py` succeeds

## 2. Confirm package metadata

Before publishing, verify:

- `pyproject.toml` version is correct
- `project.urls` point at the public repo
- `uqa` dependency range matches the `uqa` release you are publishing
- `README.md` and `docs/` reflect the current macro vocabulary

## 3. Build fresh artifacts

```bash
rm -rf dist
uv build --all-packages
ls -1 dist
```

You should see all four artifacts:

- `uqa-<version>.tar.gz`
- `uqa-<version>-py3-none-any.whl`
- `anamnesis-<version>.tar.gz`
- `anamnesis-<version>-py3-none-any.whl`

## 4. Decide how `uv publish` will authenticate

Recommended choices:

1. **Trusted publishing** for CI release pipelines
2. **PyPI token** for manual publishing

With a token-based flow, export:

```bash
export UV_PUBLISH_TOKEN="pypi-***"
```

If you need TestPyPI, pass `--publish-url` to `uv publish`.

Official references:

- uv packaging/publishing guide: https://docs.astral.sh/uv/guides/package/
- uv `publish` reference: https://docs.astral.sh/uv/reference/cli/#uv-publish
- PyPI trusted publishing: https://docs.pypi.org/trusted-publishers/

## 5. Publish in order

Publish `uqa` first:

```bash
uv publish \
  dist/uqa-*.tar.gz \
  dist/uqa-*.whl
```

Then publish `anamnesis`:

```bash
uv publish \
  dist/anamnesis-*.tar.gz \
  dist/anamnesis-*.whl
```

## 6. Post-publish spot checks

After both uploads complete:

```bash
python -m venv /tmp/anamnesis-pypi-check
source /tmp/anamnesis-pypi-check/bin/activate
python -m pip install -U pip
python -m pip install anamnesis
anamnesis --help
anamnesis-init --help
```

Optional deeper check:

```bash
python scripts/smoke_client_connections.py
```

## 7. Release notes sanity

Before announcing the release, confirm:

- GitHub tag matches the published versions
- release notes mention the mandatory UQA dependency
- release notes mention the public macro vocabulary:
  - `@survey`
  - `@synopsis`
  - `@artifact`
  - `@chronicle`
  - `@cadence`
  - `@lineage`
  - `@crossroads`
  - `@relay`
  - `@thesis`
  - `@vitals`
