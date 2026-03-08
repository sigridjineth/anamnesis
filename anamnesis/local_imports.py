from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ensure_repo_on_syspath


def import_uqa_engine(repo_root: Path | None = None):
    ensure_repo_on_syspath(repo_root)
    from uqa.engine import Engine

    return Engine


def import_uqa_sql_compiler(repo_root: Path | None = None):
    ensure_repo_on_syspath(repo_root)
    from uqa.sql.compiler import SQLCompiler

    return SQLCompiler


def import_uqa_graph_types(repo_root: Path | None = None) -> tuple[Any, Any]:
    ensure_repo_on_syspath(repo_root)
    from uqa.core.types import Edge, Vertex

    return Vertex, Edge
