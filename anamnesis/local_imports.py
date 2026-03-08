from __future__ import annotations

from pathlib import Path

from .config import ensure_repo_on_syspath


def import_uqa_engine(repo_root: Path | None = None):
    ensure_repo_on_syspath(repo_root)
    from uqa.engine import Engine

    return Engine
