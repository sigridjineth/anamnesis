from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_TEXT_HINTS = ("content", "text", "body", "summary", "title", "message", "query", "reason", "decision", "path", "file", "name")
_FILE_HINTS = ("path", "file_path", "source_path", "target_path", "filepath", "filename")
_TIME_HINTS = ("created_at", "updated_at", "timestamp", "ts", "event_ts", "started_at", "ended_at")
_READ_ONLY_SQL = re.compile(
    r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA)\b",
    re.IGNORECASE | re.DOTALL,
)


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _is_textish(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _TEXT_HINTS)


def _is_fileish(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _FILE_HINTS)


def _time_columns(columns: list[str]) -> list[str]:
    return [name for name in columns if any(token in name.lower() for token in _TIME_HINTS)]


class GenericSQLiteExplorer:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def orient(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            objects = []
            for row in rows:
                cols = db.execute(f"PRAGMA table_info({_quote_ident(row['name'])})").fetchall()
                objects.append(
                    {
                        "name": row["name"],
                        "kind": row["type"],
                        "columns": [
                            {
                                "name": col[1],
                                "type": col[2],
                                "not_null": bool(col[3]),
                                "default": col[4],
                                "primary_key": bool(col[5]),
                            }
                            for col in cols
                        ],
                    }
                )
        return {
            "target": str(self.db_path),
            "objects": objects,
            "metadata": {"object_count": len(objects)},
        }

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        pattern = f"%{query}%"
        hits: list[dict[str, Any]] = []
        with closing(self._connect()) as db:
            for obj in self.orient()["objects"]:
                cols = [col["name"] for col in obj["columns"] if _is_textish(col["name"])]
                if not cols:
                    continue
                remaining = limit - len(hits)
                if remaining <= 0:
                    break
                where = " OR ".join(f"{_quote_ident(col)} LIKE ?" for col in cols[:5])
                sql = f"SELECT * FROM {_quote_ident(obj['name'])} WHERE {where} LIMIT {remaining}"
                params = tuple(pattern for _ in cols[:5])
                for row in db.execute(sql, params).fetchall():
                    row_dict = dict(row)
                    preview = next((str(row_dict[col]) for col in cols if row_dict.get(col)), None)
                    hits.append({"source": obj["name"], "row": row_dict, "preview": preview})
        return {"target": str(self.db_path), "hits": hits, "metadata": {"query": query, "limit": limit}}

    def trace_file(self, path_query: str, *, limit: int = 25) -> dict[str, Any]:
        pattern = f"%{path_query}%"
        hits: list[dict[str, Any]] = []
        with closing(self._connect()) as db:
            for obj in self.orient()["objects"]:
                cols = [col["name"] for col in obj["columns"] if _is_fileish(col["name"])]
                if not cols:
                    continue
                remaining = limit - len(hits)
                if remaining <= 0:
                    break
                where = " OR ".join(f"{_quote_ident(col)} LIKE ?" for col in cols)
                sql = f"SELECT * FROM {_quote_ident(obj['name'])} WHERE {where} LIMIT {remaining}"
                params = tuple(pattern for _ in cols)
                for row in db.execute(sql, params).fetchall():
                    row_dict = dict(row)
                    preview = next((str(row_dict[col]) for col in cols if row_dict.get(col)), None)
                    hits.append({"source": obj["name"], "row": row_dict, "preview": preview})
        return {"target": str(self.db_path), "hits": hits, "metadata": {"path": path_query, "limit": limit}}

    def digest(self, *, days: int = 7) -> dict[str, Any]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        rows: list[dict[str, Any]] = []
        with closing(self._connect()) as db:
            for obj in self.orient()["objects"]:
                total = db.execute(f"SELECT COUNT(*) FROM {_quote_ident(obj['name'])}").fetchone()[0]
                time_cols = _time_columns([col["name"] for col in obj["columns"]])
                recent_rows = None
                if time_cols:
                    col = time_cols[0]
                    recent_rows = db.execute(
                        f"SELECT COUNT(*) FROM {_quote_ident(obj['name'])} WHERE {_quote_ident(col)} >= ?",
                        (cutoff.isoformat(),),
                    ).fetchone()[0]
                rows.append({"table": obj["name"], "rows": total, "recent_rows": recent_rows})
        return {"rows": rows, "metadata": {"window_days": days, "since": cutoff.isoformat()}}

    def sql(self, query: str, *, read_only: bool = True) -> dict[str, Any]:
        if read_only and not _READ_ONLY_SQL.match(query.strip()):
            raise ValueError("Only read-only SELECT/WITH/EXPLAIN/PRAGMA queries are allowed")
        with closing(self._connect()) as db:
            rows = [dict(row) for row in db.execute(query).fetchall()]
        return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}
