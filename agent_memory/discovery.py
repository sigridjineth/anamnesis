from __future__ import annotations

import sys
from pathlib import Path


def add_repo_to_syspath(repo_root: Path | None) -> None:
    if repo_root is None:
        return
    repo_str = str(repo_root.resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


def default_flex_cell_path(cell_name: str) -> Path:
    return Path.home() / ".flex" / "cells" / f"{cell_name}.db"
