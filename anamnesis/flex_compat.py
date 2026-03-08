from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any, Sequence

from anamnesis.config import Settings
from anamnesis.flex_projection import FlexCellProjector
from anamnesis.init_cli import main as init_main

DEFAULT_CELL = "claude_code"


def workspace_settings(workspace_root: str | Path | None = None) -> Settings:
    root = Path(workspace_root or Path.cwd()).resolve()
    return Settings.from_env(workspace_root=root)


def resolve_raw_db_path(
    cell: str = DEFAULT_CELL,
    *,
    settings: Settings | None = None,
    db_path: str | Path | None = None,
) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser().resolve()
    settings = settings or workspace_settings()
    if cell == DEFAULT_CELL:
        return settings.raw_db_path
    candidates = [
        settings.workspace_root / ".anamnesis" / "cells" / f"{cell}.db",
        settings.workspace_root / ".flex" / "cells" / f"{cell}.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def resolve_flex_cell_path(
    cell: str = DEFAULT_CELL,
    *,
    settings: Settings | None = None,
) -> Path:
    settings = settings or workspace_settings()
    return (settings.workspace_root / ".flex" / "cells" / f"{cell}.db").resolve()


def _should_use_existing_flex_cell(
    cell: str,
    *,
    settings: Settings,
    db_path: str | Path | None = None,
) -> bool:
    if db_path is not None:
        candidate = Path(db_path).expanduser().resolve()
        return candidate == resolve_flex_cell_path(cell, settings=settings)
    return cell != DEFAULT_CELL and resolve_flex_cell_path(cell, settings=settings).exists()


def _ensure_flex_cell(
    *,
    cell: str,
    settings: Settings,
    db_path: str | Path | None = None,
) -> Path:
    if _should_use_existing_flex_cell(cell, settings=settings, db_path=db_path):
        return resolve_flex_cell_path(cell, settings=settings)
    raw_db = resolve_raw_db_path(cell, settings=settings, db_path=db_path)
    projector = FlexCellProjector(
        settings=settings,
        raw_db_path=raw_db,
        sidecar_path=raw_db.with_suffix(".uqa.db"),
        cell_name=cell,
    )
    return projector.ensure_ready()


def sync_flex_cell(
    *,
    cell: str = DEFAULT_CELL,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    settings = workspace_settings(workspace_root)
    if _should_use_existing_flex_cell(cell, settings=settings, db_path=db_path):
        return {
            "cell": cell,
            "cell_path": str(resolve_flex_cell_path(cell, settings=settings)),
            "backend": "existing-flex-cell",
            "status": "passthrough",
        }
    raw_db = resolve_raw_db_path(cell, settings=settings, db_path=db_path)
    projector = FlexCellProjector(
        settings=settings,
        raw_db_path=raw_db,
        sidecar_path=raw_db.with_suffix(".uqa.db"),
        cell_name=cell,
    )
    return projector.rebuild() if projector._is_stale() else {  # noqa: SLF001 - deliberate compat path
        "cell": cell,
        "cell_path": str(projector.ensure_ready()),
        "backend": "uqa->flex-projection",
        "status": "fresh",
    }


def execute_flex_query_text(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> str:
    settings = workspace_settings(workspace_root)
    _ensure_flex_cell(cell=cell, settings=settings, db_path=db_path)
    from anamnesis.getflex_runtime import GetFlexRuntime

    runtime = GetFlexRuntime(settings.workspace_root)
    merged_query = merge_params_into_query(query, params).strip()
    return runtime.execute_cli_query(cell_name=cell, query=merged_query)


def execute_flex_mcp_text(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> str:
    settings = workspace_settings(workspace_root)
    _ensure_flex_cell(cell=cell, settings=settings, db_path=db_path)
    from anamnesis.getflex_runtime import GetFlexRuntime

    runtime = GetFlexRuntime(settings.workspace_root)
    return runtime.execute_mcp_query(cell_name=cell, query=query, params=params)


def execute_flex_query(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Any:
    text = execute_flex_query_text(
        query,
        cell=cell,
        params=params,
        workspace_root=workspace_root,
        db_path=db_path,
    )
    return json.loads(text)


def merge_params_into_query(query: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return query
    normalized = query.strip()
    if not normalized.lstrip("!").startswith("@"):
        return normalized
    suffix = " ".join(f"{key}={_stringify_param(value)}" for key, value in params.items())
    return f"{normalized} {suffix}".strip()


def parse_preset_query(query: str) -> tuple[str, dict[str, str], list[str]]:
    tokens = shlex.split(query.strip())
    if not tokens or not tokens[0].startswith("@"):
        raise ValueError("Expected a Flex preset query starting with '@'")
    preset = tokens[0]
    args: dict[str, str] = {}
    positional: list[str] = []
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            args[key.strip().lower()] = value.strip()
        else:
            positional.append(token)
    return preset.lower(), args, positional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flex",
        description="Your AI sessions, searchable forever.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="Wire hooks, daemon, and MCP for Claude Code")
    init.add_argument("--local", action="store_true", help="Use local CPU for embeddings, skip Nomic prompt")
    init.add_argument("--nomic-key", help="Nomic API key (skips interactive prompt)")

    search = subparsers.add_parser("search", help="Search your sessions")
    search.add_argument("query", help="SQL query, @preset, or vec_ops expression")
    search.add_argument("--cell", default=DEFAULT_CELL, help="Cell to query (default: claude_code)")
    search.add_argument("--db")
    search.add_argument("--json", action="store_true", help="Output raw JSON")

    sync = subparsers.add_parser("sync", help="Bring code, data, and services into parity")
    sync.add_argument("--cell", default=None, help="Sync specific cell only (default: all)")
    sync.add_argument("--full", action="store_true", help="Also rebuild enrichments (~2min)")
    sync.add_argument("--db")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "init":
        forwarded: list[str] = []
        if args.local:
            forwarded.append("--workspace-root")
            forwarded.append(str(Path.cwd()))
        return int(init_main(forwarded))

    if args.command == "sync":
        target_cells = [args.cell] if args.cell else [DEFAULT_CELL]
        results = [
            sync_flex_cell(cell=cell, workspace_root=Path.cwd(), db_path=args.db)
            for cell in target_cells
        ]
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, default=str))
        return 0

    if args.command == "search":
        text = execute_flex_query_text(
            args.query,
            cell=args.cell,
            workspace_root=Path.cwd(),
            db_path=args.db,
        )
        print(text)
        return 0

    parser.print_help()
    return 0


def _stringify_param(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
