from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_memory.config import WorkspaceConfig, ensure_repo_on_syspath
from agent_memory.contracts import QueryResponse, SchemaObject, SchemaSummary, SearchHit, SearchResponse

_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA)\b", re.IGNORECASE | re.DOTALL)
_TEXT_HINTS = (
    "content", "text", "body", "summary", "title", "message", "query",
    "reason", "decision", "path", "file", "name",
)
_FILE_HINTS = ("path", "file_path", "source_path", "target_path", "filepath", "filename")
_TIME_HINTS = ("created_at", "updated_at", "timestamp", "ts", "event_ts", "started_at", "ended_at")


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _is_read_only_sql(sql: str) -> bool:
    return bool(_READ_ONLY_SQL.match(sql.strip()))


def _is_textish_column(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _TEXT_HINTS)


def _is_file_column(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _FILE_HINTS)


def _is_time_column(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in _TIME_HINTS)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=UTC)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


class FlexBackend:
    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        self.config = config or WorkspaceConfig.from_workspace()

    def available_cells(self) -> list[dict[str, Any]]:
        try:
            ensure_repo_on_syspath(self.config.flex_repo)
            from flex.registry import list_cells

            return list_cells()
        except Exception:
            return []

    def resolve_target(self, cell: str | None = None, db_path: str | None = None) -> tuple[Path, str | None]:
        if db_path:
            path = Path(db_path).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"Flex target does not exist: {path}")
            return path, cell

        if self.config.default_flex_db_path is not None:
            return self.config.default_flex_db_path, cell or self.config.default_flex_cell

        cell_name = cell or self.config.default_flex_cell
        ensure_repo_on_syspath(self.config.flex_repo)
        try:
            from flex.registry import resolve_cell
        except Exception as exc:
            raise RuntimeError(
                "Flex registry import failed. Provide db_path explicitly or ensure the cloned flex repo is available."
            ) from exc

        path = resolve_cell(cell_name)
        if path is None:
            raise FileNotFoundError(f"Flex cell '{cell_name}' is not registered")
        return path.resolve(), cell_name

    def _connect(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{path}?mode=ro"
        db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    def _list_objects(self, db: sqlite3.Connection) -> list[SchemaObject]:
        rows = db.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        objects: list[SchemaObject] = []
        for row in rows:
            cols = db.execute(f"PRAGMA table_info({_quote_ident(row['name'])})").fetchall()
            objects.append(
                SchemaObject(
                    name=row["name"],
                    kind=row["type"],
                    columns=[
                        {
                            "name": col[1],
                            "type": col[2],
                            "not_null": bool(col[3]),
                            "default": col[4],
                            "primary_key": bool(col[5]),
                        }
                        for col in cols
                    ],
                )
            )
        return objects

    def orient(self, cell: str | None = None, db_path: str | None = None) -> SchemaSummary:
        if cell is None and db_path is None and self.config.default_flex_db_path is None:
            return SchemaSummary(
                backend="flex",
                target=None,
                metadata={
                    "repo": str(self.config.flex_repo),
                    "available_cells": self.available_cells(),
                    "hint": "Provide a registered cell name or db_path to inspect a concrete target.",
                },
            )

        path, resolved_cell = self.resolve_target(cell=cell, db_path=db_path)
        with self._connect(path) as db:
            objects = self._list_objects(db)

        return SchemaSummary(
            backend="flex",
            target=str(path),
            objects=objects,
            metadata={
                "cell": resolved_cell,
                "object_count": len(objects),
            },
        )

    def sql(self, query: str, cell: str | None = None, db_path: str | None = None, read_only: bool = True) -> QueryResponse:
        if read_only and not _is_read_only_sql(query):
            raise ValueError("Flex backend only allows read-only SELECT/WITH/EXPLAIN/PRAGMA queries")

        path, resolved_cell = self.resolve_target(cell=cell, db_path=db_path)
        with self._connect(path) as db:
            rows = [dict(row) for row in db.execute(query).fetchall()]
        columns = list(rows[0].keys()) if rows else []
        return QueryResponse(
            backend="flex",
            target=str(path),
            columns=columns,
            rows=rows,
            metadata={"cell": resolved_cell},
        )

    def _iter_candidate_objects(
        self,
        db: sqlite3.Connection,
        *,
        column_predicate,
        preferred_names: tuple[str, ...] = ("messages", "chunks", "sources", "sessions"),
    ) -> list[tuple[str, list[str]]]:
        candidates: list[tuple[str, list[str]]] = []
        for obj in self._list_objects(db):
            cols = [col["name"] for col in obj.columns if column_predicate(col["name"])]
            if cols:
                candidates.append((obj.name, cols))

        def priority(item: tuple[str, list[str]]) -> tuple[int, str]:
            name = item[0]
            try:
                return (preferred_names.index(name), name)
            except ValueError:
                return (len(preferred_names), name)

        return sorted(candidates, key=priority)

    def search(self, query: str, cell: str | None = None, db_path: str | None = None, limit: int = 10) -> SearchResponse:
        path, resolved_cell = self.resolve_target(cell=cell, db_path=db_path)
        pattern = f"%{query}%"
        hits: list[SearchHit] = []
        with self._connect(path) as db:
            candidates = self._iter_candidate_objects(db, column_predicate=_is_textish_column)
            for table_name, cols in candidates:
                remaining = limit - len(hits)
                if remaining <= 0:
                    break
                where = " OR ".join(f"{_quote_ident(col)} LIKE ?" for col in cols[:5])
                sql = (
                    f"SELECT * FROM {_quote_ident(table_name)} "
                    f"WHERE {where} LIMIT {remaining}"
                )
                params = tuple(pattern for _ in cols[:5])
                for row in db.execute(sql, params).fetchall():
                    row_dict = dict(row)
                    preview = next(
                        (str(row_dict[col]) for col in cols if row_dict.get(col)),
                        None,
                    )
                    hits.append(SearchHit(source=table_name, row=row_dict, preview=preview))

        return SearchResponse(
            backend="flex",
            target=str(path),
            hits=hits,
            metadata={
                "cell": resolved_cell,
                "query": query,
                "limit": limit,
            },
        )

    def trace_file(self, path_query: str, cell: str | None = None, db_path: str | None = None, limit: int = 25) -> SearchResponse:
        path, resolved_cell = self.resolve_target(cell=cell, db_path=db_path)
        hits: list[SearchHit] = []
        pattern = f"%{path_query}%"
        with self._connect(path) as db:
            candidates = self._iter_candidate_objects(db, column_predicate=_is_file_column, preferred_names=("files", "chunks", "messages", "sources"))
            for table_name, cols in candidates:
                remaining = limit - len(hits)
                if remaining <= 0:
                    break
                where = " OR ".join(f"{_quote_ident(col)} LIKE ?" for col in cols)
                sql = f"SELECT * FROM {_quote_ident(table_name)} WHERE {where} LIMIT {remaining}"
                params = tuple(pattern for _ in cols)
                for row in db.execute(sql, params).fetchall():
                    row_dict = dict(row)
                    preview = next(
                        (str(row_dict[col]) for col in cols if row_dict.get(col)),
                        None,
                    )
                    hits.append(SearchHit(source=table_name, row=row_dict, preview=preview))

        return SearchResponse(
            backend="flex",
            target=str(path),
            hits=hits,
            metadata={
                "cell": resolved_cell,
                "path": path_query,
                "limit": limit,
            },
        )

    def trace_decision(self, query: str, cell: str | None = None, db_path: str | None = None, limit: int = 25) -> SearchResponse:
        response = self.search(query=query, cell=cell, db_path=db_path, limit=limit)
        response.metadata["focus"] = "decision"
        return response

    def digest(self, days: int = 7, cell: str | None = None, db_path: str | None = None) -> QueryResponse:
        path, resolved_cell = self.resolve_target(cell=cell, db_path=db_path)
        cutoff = datetime.now(tz=UTC) - timedelta(days=days)
        summary_rows: list[dict[str, Any]] = []
        recent_examples: list[dict[str, Any]] = []

        with self._connect(path) as db:
            for obj in self._list_objects(db):
                time_cols = [col["name"] for col in obj.columns if _is_time_column(col["name"])]
                total_count = db.execute(f"SELECT COUNT(*) AS n FROM {_quote_ident(obj.name)}").fetchone()["n"]
                recent_count = None
                if time_cols:
                    order_col = time_cols[0]
                    rows = db.execute(
                        f"SELECT * FROM {_quote_ident(obj.name)} ORDER BY {_quote_ident(order_col)} DESC LIMIT 250"
                    ).fetchall()
                    parsed_rows = [dict(row) for row in rows]
                    recent_count = 0
                    for row in parsed_rows:
                        ts = _parse_timestamp(row.get(order_col))
                        if ts is not None and ts >= cutoff:
                            recent_count += 1
                            if len(recent_examples) < 5:
                                recent_examples.append({
                                    "table": obj.name,
                                    "time_column": order_col,
                                    "row": row,
                                })
                summary_rows.append(
                    {
                        "table": obj.name,
                        "kind": obj.kind,
                        "total_rows": total_count,
                        "recent_rows": recent_count,
                        "time_columns": time_cols,
                    }
                )

        return QueryResponse(
            backend="flex",
            target=str(path),
            columns=["table", "kind", "total_rows", "recent_rows", "time_columns"],
            rows=summary_rows,
            metadata={
                "cell": resolved_cell,
                "window_days": days,
                "recent_examples": recent_examples,
            },
        )
