from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any

from .flex_bridge import discover_flex_cells
from .models import DecisionRecord, FileTouchRecord, SearchHit
from .storage import RawMemoryStore
from .uqa_sidecar import UQASidecar


class MemoryQueryService:
    def __init__(self, store: RawMemoryStore):
        self.store = store

    def orient(self, project_id: str | None = None) -> dict[str, Any]:
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        counts = {
            "sessions": self._scalar(f"SELECT COUNT(*) FROM sessions {where}", params),
            "events": self._scalar(f"SELECT COUNT(*) FROM events {where}", params),
            "file_touches": self._scalar(
                "SELECT COUNT(*) FROM file_touches ft JOIN events e ON e.id = ft.event_id "
                + ("WHERE e.project_id = ?" if project_id else ""),
                params,
            ),
        }
        timers = self.store.fetchone(
            f"SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM events {where}", params
        )
        tables = [row[0] for row in self.store.fetchall(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
        )]
        by_agent = [
            dict(row)
            for row in self.store.fetchall(
                f"SELECT agent, COUNT(*) AS event_count FROM events {where} GROUP BY agent ORDER BY event_count DESC",
                params,
            )
        ]
        flex_cells = discover_flex_cells()
        uqa_status = UQASidecar(self.store.db_path).status()
        return {
            "project_id": project_id,
            "tables": tables,
            "counts": counts,
            "window": {
                "first_ts": timers["first_ts"] if timers else None,
                "last_ts": timers["last_ts"] if timers else None,
            },
            "agents": by_agent,
            "flex_cells": flex_cells,
            "uqa": uqa_status,
            "presets": [
                "orient",
                "search",
                "trace_file",
                "trace_decision",
                "digest",
                "sql",
            ],
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
        backend: str = "auto",
    ) -> list[dict[str, Any]]:
        if backend in {"auto", "uqa"}:
            sidecar = UQASidecar(self.store.db_path)
            if sidecar.available():
                results = sidecar.search(query, limit=limit)
                if results or backend == "uqa":
                    return results
        like = f"%{query}%"
        clauses = ["(COALESCE(content, '') LIKE ? OR COALESCE(tool_name, '') LIKE ? OR COALESCE(target_path, '') LIKE ?)"]
        params: list[Any] = [like, like, like]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        sql = f"""
            SELECT id, session_id, ts, kind, content, target_path,
                   CASE
                       WHEN kind = 'prompt' THEN 3.0
                       WHEN kind = 'assistant_message' THEN 2.0
                       ELSE 1.0
                   END AS score
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY score DESC, ts DESC
            LIMIT ?
        """
        params.append(limit)
        return [SearchHit(**dict(row)).to_dict() for row in self.store.fetchall(sql, tuple(params))]

    def trace_file(self, path: str, *, limit: int = 20) -> dict[str, Any]:
        rows = self.store.fetchall(
            """
            SELECT e.session_id, e.ts, e.kind, ft.path, ft.operation, e.content
            FROM file_touches ft
            JOIN events e ON e.id = ft.event_id
            WHERE ft.path = ?
            ORDER BY e.ts DESC
            LIMIT ?
            """,
            (path, limit),
        )
        return {
            "path": path,
            "touches": [FileTouchRecord(**dict(row)).to_dict() for row in rows],
        }

    def trace_decision(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        like = f"%{query}%"
        rows = self.store.fetchall(
            """
            SELECT session_id,
                   MIN(ts) AS first_seen_at,
                   MAX(ts) AS last_seen_at,
                   COUNT(*) AS event_count,
                   SUBSTR(MAX(COALESCE(content, '')), 1, 240) AS excerpt
            FROM events
            WHERE COALESCE(content, '') LIKE ?
            GROUP BY session_id
            ORDER BY event_count DESC, last_seen_at DESC
            LIMIT ?
            """,
            (like, limit),
        )
        return {
            "query": query,
            "sessions": [DecisionRecord(**dict(row)).to_dict() for row in rows],
        }

    def digest(self, *, days: int = 7) -> dict[str, Any]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        sessions = [
            dict(row)
            for row in self.store.fetchall(
                """
                SELECT session_id, agent, project_id, COUNT(*) AS event_count,
                       MIN(ts) AS started_at, MAX(ts) AS last_seen_at
                FROM events
                WHERE ts >= ?
                GROUP BY session_id, agent, project_id
                ORDER BY last_seen_at DESC
                """,
                (cutoff,),
            )
        ]
        top_files = [
            dict(row)
            for row in self.store.fetchall(
                """
                SELECT ft.path, COUNT(*) AS touches
                FROM file_touches ft
                JOIN events e ON e.id = ft.event_id
                WHERE e.ts >= ?
                GROUP BY ft.path
                ORDER BY touches DESC, ft.path ASC
                LIMIT 10
                """,
                (cutoff,),
            )
        ]
        return {
            "days": days,
            "since": cutoff,
            "sessions": sessions,
            "top_files": top_files,
        }

    def sql(self, sql: str) -> dict[str, Any]:
        _assert_read_only(sql)
        rows = self.store.fetchall(sql)
        return {
            "columns": list(rows[0].keys()) if rows else [],
            "rows": [dict(row) for row in rows],
        }

    def _scalar(self, sql: str, params: tuple = ()) -> int:
        row = self.store.fetchone(sql, params)
        if row is None:
            return 0
        return int(row[0])


def _assert_read_only(sql: str) -> None:
    stripped = sql.strip().lower()
    allowed = ("select", "with", "explain", "pragma")
    if not stripped.startswith(allowed):
        raise ValueError("Only read-only SQL is allowed")
