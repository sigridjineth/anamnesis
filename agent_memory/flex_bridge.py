from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .local_imports import optional_import


def discover_flex_cells() -> list[dict[str, Any]]:
    registry_db = Path.home() / ".flex" / "registry.db"
    if not registry_db.exists():
        return []
    registry_mod = optional_import("flex.registry", checkout_name="flex")
    if registry_mod is not None:
        try:
            return registry_mod.list_cells()
        except Exception:
            pass
    with closing(sqlite3.connect(registry_db)) as db:
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT id, name, path, corpus_path, cell_type, description FROM cells ORDER BY name"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(row) for row in rows]
