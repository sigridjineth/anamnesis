from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any, Sequence

from anamnesis.config import Settings
from anamnesis.init_cli import main as init_main
from anamnesis.mcp_server import main as mcp_main
from anamnesis.projected_cells import ProjectedCellProjector
from anamnesis.preset_runtime import PresetRuntime
from anamnesis.service import MemoryService

DEFAULT_CELL = "claude_code"

PUBLIC_PRESET_TO_RUNTIME: dict[str, str] = {
    "@survey": "@orient",
    "@synopsis": "@digest",
    "@artifact": "@file",
    "@chronicle": "@story",
    "@cadence": "@sprints",
    "@lineage": "@genealogy",
    "@crossroads": "@bridges",
    "@relay": "@delegation-tree",
    "@vitals": "@health",
}
BUILTIN_PRESET_NAMES: frozenset[str] = frozenset({"@thesis"})
LEGACY_PRESET_ALIASES: dict[str, str] = {
    **{runtime: public for public, runtime in PUBLIC_PRESET_TO_RUNTIME.items()},
    "@decision": "@thesis",
}


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
    candidate = settings.workspace_root / ".anamnesis" / "cells" / f"{cell}.db"
    return candidate.resolve()


def resolve_projected_cell_path(
    cell: str = DEFAULT_CELL,
    *,
    settings: Settings | None = None,
) -> Path:
    settings = settings or workspace_settings()
    return (settings.workspace_root / ".anamnesis" / "cells" / f"{cell}.db").resolve()


def _should_use_existing_projected_cell(
    cell: str,
    *,
    settings: Settings,
    db_path: str | Path | None = None,
) -> bool:
    current_path = resolve_projected_cell_path(cell, settings=settings)
    if db_path is not None:
        candidate = Path(db_path).expanduser().resolve()
        return candidate == current_path
    if cell == DEFAULT_CELL:
        return False
    return current_path.exists()


def _existing_projected_cell_path(cell: str, *, settings: Settings) -> Path:
    return resolve_projected_cell_path(cell, settings=settings)


def _ensure_projected_cell(
    *,
    cell: str,
    settings: Settings,
    db_path: str | Path | None = None,
) -> Path:
    if _should_use_existing_projected_cell(cell, settings=settings, db_path=db_path):
        return _existing_projected_cell_path(cell, settings=settings)
    raw_db = resolve_raw_db_path(cell, settings=settings, db_path=db_path)
    projector = ProjectedCellProjector(
        settings=settings,
        raw_db_path=raw_db,
        sidecar_path=raw_db.with_suffix(".uqa.db"),
        cell_name=cell,
    )
    return projector.ensure_ready()


def sync_projected_cell(
    *,
    cell: str = DEFAULT_CELL,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    settings = workspace_settings(workspace_root)
    if _should_use_existing_projected_cell(cell, settings=settings, db_path=db_path):
        return {
            "cell": cell,
            "cell_path": str(_existing_projected_cell_path(cell, settings=settings)),
            "backend": "existing-anamnesis-cell",
            "status": "passthrough",
        }
    raw_db = resolve_raw_db_path(cell, settings=settings, db_path=db_path)
    projector = ProjectedCellProjector(
        settings=settings,
        raw_db_path=raw_db,
        sidecar_path=raw_db.with_suffix(".uqa.db"),
        cell_name=cell,
    )
    return projector.rebuild() if projector._is_stale() else {  # noqa: SLF001 - deliberate projection cache check
        "cell": cell,
        "cell_path": str(projector.ensure_ready()),
        "backend": "uqa->anamnesis-projection",
        "status": "fresh",
    }


def translate_query_text(query: str) -> str:
    stripped = query.strip()
    if not stripped:
        return stripped
    tokens = stripped.split(maxsplit=1)
    command = tokens[0]
    remainder = tokens[1] if len(tokens) > 1 else ""
    bang_prefix = "!" if command.startswith("!") else ""
    preset = command[1:] if bang_prefix else command
    normalized = preset.lower()
    if normalized in LEGACY_PRESET_ALIASES:
        replacement = LEGACY_PRESET_ALIASES[normalized]
        raise ValueError(f"Legacy preset '{normalized}' is no longer supported; use '{replacement}' instead")
    runtime_preset = PUBLIC_PRESET_TO_RUNTIME.get(normalized, normalized)
    translated = f"{bang_prefix}{runtime_preset}"
    return f"{translated} {remainder}".strip()


def merge_params_into_query(query: str, params: dict[str, Any] | None = None) -> str:
    normalized = query.strip()
    if not params:
        return normalized
    if not normalized.lstrip("!").startswith("@"):
        return normalized
    suffix = " ".join(f"{key}={_stringify_param(value)}" for key, value in params.items())
    return f"{normalized} {suffix}".strip()


def _split_macro_query(query: str) -> tuple[str, dict[str, str], list[str]]:
    tokens = shlex.split(query.strip())
    if not tokens:
        raise ValueError("Expected an Anamnesis macro starting with '@'")
    preset = tokens[0].lower()
    if preset.startswith("!"):
        preset = preset[1:]
    if not preset.startswith("@"):
        raise ValueError("Expected an Anamnesis macro starting with '@'")
    args: dict[str, str] = {}
    positional: list[str] = []
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            args[key.strip().lower()] = value.strip()
        else:
            positional.append(token)
    return preset, args, positional


def parse_macro_query(query: str) -> tuple[str, dict[str, str], list[str]]:
    preset, args, positional = _split_macro_query(query)
    translated = translate_query_text(preset)
    if translated == preset and preset not in PUBLIC_PRESET_TO_RUNTIME and preset not in BUILTIN_PRESET_NAMES:
        raise ValueError(f"Unknown Anamnesis macro: {preset}")
    return preset, args, positional


def _int_arg(args: dict[str, str], name: str, default: int) -> int:
    raw = args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - simple validation path
        raise ValueError(f"@thesis parameter '{name}' must be an integer") from exc


def _service_db_path(db_path: str | Path | None = None) -> str | None:
    if db_path is None:
        return None
    return str(Path(db_path).expanduser().resolve())


def _execute_builtin_macro_text(
    query: str,
    *,
    settings: Settings,
    db_path: str | Path | None = None,
) -> str | None:
    if not query.strip():
        return None
    try:
        preset, args, positional = _split_macro_query(query)
    except ValueError:
        return None
    if preset not in BUILTIN_PRESET_NAMES:
        return None

    topic = args.get("query") or args.get("concept") or args.get("topic") or " ".join(positional).strip()
    if not topic:
        raise ValueError("@thesis requires query=<text> or a positional topic")

    result = MemoryService(settings=settings).trace_decision(
        topic,
        db_path=_service_db_path(db_path),
        limit=_int_arg(args, "limit", 10),
        project_id=args.get("project_id"),
    )
    return json.dumps(result, indent=2, default=str)


def execute_query_text(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> str:
    settings = workspace_settings(workspace_root)
    merged_query = merge_params_into_query(query, params).strip()
    builtin = _execute_builtin_macro_text(merged_query, settings=settings, db_path=db_path)
    if builtin is not None:
        return builtin
    _ensure_projected_cell(cell=cell, settings=settings, db_path=db_path)
    runtime = PresetRuntime(settings.workspace_root)
    translated_query = translate_query_text(merged_query)
    return runtime.execute_cli_query(cell_name=cell, query=translated_query)


def execute_mcp_query_text(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> str:
    settings = workspace_settings(workspace_root)
    merged_query = merge_params_into_query(query, params).strip()
    builtin = _execute_builtin_macro_text(merged_query, settings=settings, db_path=db_path)
    if builtin is not None:
        return builtin
    _ensure_projected_cell(cell=cell, settings=settings, db_path=db_path)
    runtime = PresetRuntime(settings.workspace_root)
    translated_query = translate_query_text(query)
    return runtime.execute_mcp_query(cell_name=cell, query=translated_query, params=params)


def execute_query(
    query: str,
    *,
    cell: str = DEFAULT_CELL,
    params: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Any:
    text = execute_query_text(
        query,
        cell=cell,
        params=params,
        workspace_root=workspace_root,
        db_path=db_path,
    )
    return json.loads(text)


def _add_common_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    query = subparsers.add_parser("search", help="Search your AI memory")
    query.add_argument("query", help="SQL, Anamnesis macro, or vector expression")
    query.add_argument("--cell", default=DEFAULT_CELL, help="Cell to query (default: claude_code)")
    query.add_argument("--db")
    query.add_argument("--json", action="store_true", help="Reserved for raw JSON output")

    sync = subparsers.add_parser("sync", help="Refresh Anamnesis projected cells from the raw store")
    sync.add_argument("--cell", default=None, help="Sync specific cell only (default: all)")
    sync.add_argument("--db")

    init = subparsers.add_parser("init", help="Generate Claude/Codex/OpenCode config for this workspace")
    init.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to anamnesis-init")

    mcp = subparsers.add_parser("mcp", help="Run the Anamnesis MCP server")
    mcp.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to anamnesis-mcp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anamnesis",
        description="Anamnesis — searchable shared memory for Claude, Codex, and OpenCode.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _add_common_subcommands(subparsers)
    return parser


def _strip_remainder_prefix(args: Sequence[str]) -> list[str]:
    remainder = list(args)
    if remainder and remainder[0] == "--":
        return remainder[1:]
    return remainder


def _run_cli(args: argparse.Namespace) -> int:
    if args.command == "init":
        return int(init_main(_strip_remainder_prefix(args.args)))

    if args.command == "mcp":
        return int(mcp_main(_strip_remainder_prefix(args.args)) or 0)

    if args.command == "sync":
        target_cells = [args.cell] if args.cell else [DEFAULT_CELL]
        results = [
            sync_projected_cell(cell=cell, workspace_root=Path.cwd(), db_path=args.db)
            for cell in target_cells
        ]
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, default=str))
        return 0

    if args.command == "search":
        text = execute_query_text(
            args.query,
            cell=args.cell,
            workspace_root=Path.cwd(),
            db_path=args.db,
        )
        print(text)
        return 0

    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command is None:
        parser.print_help()
        return 0
    return _run_cli(args)


def _stringify_param(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
