from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any

from .config import ensure_repo_on_syspath
from .local_imports import import_uqa_engine


@dataclass(slots=True)
class UQABridgeStatus:
    available: bool
    reason: str | None
    raw_db_path: str
    sidecar_path: str
    exists: bool
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UQASidecar:
    def __init__(
        self,
        raw_db_path: str | Path,
        sidecar_path: str | Path | None = None,
        *,
        repo_root: Path | None = None,
    ):
        self.raw_db_path = Path(raw_db_path).expanduser().resolve()
        self.sidecar_path = Path(sidecar_path or self.raw_db_path.with_suffix(".uqa.db")).expanduser().resolve()
        self.repo_root = repo_root

    def status(self) -> dict[str, Any]:
        available, reason = self.available()
        return UQABridgeStatus(
            available=available,
            reason=reason,
            raw_db_path=str(self.raw_db_path),
            sidecar_path=str(self.sidecar_path),
            exists=self.sidecar_path.exists(),
            stale=self._is_stale(),
        ).to_dict()

    def available(self) -> tuple[bool, str | None]:
        try:
            self._engine_class()
        except Exception as exc:
            return False, str(exc)
        return True, None

    def rebuild(self) -> dict[str, Any]:
        if not self.raw_db_path.exists():
            raise FileNotFoundError(f"raw database does not exist: {self.raw_db_path}")
        engine_cls = self._engine_class()
        rows_sessions, rows_events, rows_touches = self._read_raw_rows()
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        if self.sidecar_path.exists():
            self.sidecar_path.unlink()
        engine = engine_cls(db_path=str(self.sidecar_path), vector_dimensions=64)
        try:
            for statement in (
                "DROP TABLE IF EXISTS file_touches",
                "DROP TABLE IF EXISTS events",
                "DROP TABLE IF EXISTS sessions",
                "CREATE TABLE sessions (session_id TEXT, agent TEXT, project_id TEXT, started_at TEXT, ended_at TEXT, metadata_json TEXT)",
                "CREATE TABLE events (id TEXT, agent TEXT, session_id TEXT, project_id TEXT, ts TEXT, kind TEXT, role TEXT, content TEXT, tool_name TEXT, target_path TEXT, payload_json TEXT)",
                "CREATE TABLE file_touches (event_id TEXT, path TEXT, operation TEXT)",
            ):
                engine.sql(statement)

            self._insert_rows(engine, "sessions", rows_sessions)
            self._insert_rows(engine, "events", rows_events)
            self._insert_rows(engine, "file_touches", rows_touches)

            for statement in (
                "ANALYZE sessions",
                "ANALYZE events",
                "ANALYZE file_touches",
            ):
                try:
                    engine.sql(statement)
                except Exception:
                    pass
        finally:
            engine.close()
        return {
            "raw_db_path": str(self.raw_db_path),
            "sidecar_path": str(self.sidecar_path),
            "sessions": len(rows_sessions),
            "events": len(rows_events),
            "file_touches": len(rows_touches),
            "rebuild_reason": "missing" if not self.sidecar_path.exists() else "refresh",
        }

    def ensure_ready(self) -> None:
        available, reason = self.available()
        if not available:
            raise RuntimeError(f"UQA is required but unavailable: {reason}")
        if self._is_stale():
            self.rebuild()

    def orient(self, project_id: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        with self.engine() as engine:
            event_filter = f" WHERE project_id = {_quote(project_id)}" if project_id else ""
            event_ids = set(self._event_ids(engine, project_id=project_id)) if project_id else None
            file_touch_rows = self._rows(engine, "SELECT event_id FROM file_touches")
            counts = {
                "sessions": self._scalar(engine, "SELECT COUNT(*) AS n FROM sessions" + (f" WHERE project_id = {_quote(project_id)}" if project_id else "")),
                "events": self._scalar(engine, "SELECT COUNT(*) AS n FROM events" + event_filter),
                "file_touches": sum(1 for row in file_touch_rows if event_ids is None or row.get("event_id") in event_ids),
            }
            timers = self._one(engine, "SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM events" + event_filter)
            by_agent = self._rows(
                engine,
                "SELECT agent, COUNT(*) AS event_count FROM events"
                + event_filter
                + " GROUP BY agent ORDER BY event_count DESC",
            )
            objects = []
            for table_name, table in sorted(engine._tables.items()):  # noqa: SLF001 - UQA introspection
                objects.append(
                    {
                        "name": table_name,
                        "kind": "table",
                        "columns": [
                            {
                                "name": col.name,
                                "type": col.type_name,
                                "primary_key": col.primary_key,
                                "not_null": col.not_null,
                            }
                            for col in table.columns.values()
                        ],
                    }
                )
            return {
                "backend": "uqa",
                "project_id": project_id,
                "tables": [obj["name"] for obj in objects],
                "objects": objects,
                "counts": counts,
                "window": timers or {"first_ts": None, "last_ts": None},
                "agents": by_agent,
                "uqa": self.status(),
                "presets": ["orient", "search", "trace_file", "trace_decision", "digest", "sql"],
            }

    def search(self, query: str, *, limit: int = 10, project_id: str | None = None) -> list[dict[str, Any]]:
        self.ensure_ready()
        clause = f"text_match(content, {_quote(query)})"
        if project_id:
            clause += f" AND project_id = {_quote(project_id)}"
        sql = (
            "SELECT id, session_id, ts, kind, content, target_path, _score FROM events "
            f"WHERE {clause} ORDER BY _score DESC LIMIT {int(limit)}"
        )
        with self.engine() as engine:
            return [self._normalize_search_row(row) for row in self._rows(engine, sql)]

    def trace_file(self, path: str, *, limit: int = 20) -> dict[str, Any]:
        self.ensure_ready()
        needle = _like(path)
        with self.engine() as engine:
            touch_rows = self._rows(
                engine,
                "SELECT event_id, path, operation FROM file_touches "
                f"WHERE path = {_quote(path)} OR path LIKE {_quote(needle)}",
            )
            event_map = self._events_by_ids(engine, [str(row.get("event_id")) for row in touch_rows])
        touches = []
        for row in touch_rows:
            event = event_map.get(str(row.get("event_id")), {})
            touches.append(
                {
                    "session_id": event.get("session_id"),
                    "ts": event.get("ts"),
                    "kind": event.get("kind"),
                    "path": row.get("path"),
                    "operation": row.get("operation"),
                    "content": event.get("content"),
                }
            )
        touches.sort(key=lambda item: item.get("ts") or "", reverse=True)
        return {"path": path, "touches": touches[:limit]}

    def trace_decision(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        self.ensure_ready()
        hits = self.search(query, limit=max(limit * 20, 50))
        sessions: dict[str, dict[str, Any]] = {}
        for hit in hits:
            session_id = str(hit.get("session_id") or "")
            if not session_id:
                continue
            record = sessions.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "first_seen_at": hit.get("ts"),
                    "last_seen_at": hit.get("ts"),
                    "event_count": 0,
                    "excerpt": hit.get("content"),
                    "score": float(hit.get("score") or 0.0),
                },
            )
            record["event_count"] += 1
            ts = hit.get("ts")
            if ts and (record["first_seen_at"] is None or ts < record["first_seen_at"]):
                record["first_seen_at"] = ts
            if ts and (record["last_seen_at"] is None or ts > record["last_seen_at"]):
                record["last_seen_at"] = ts
            score = float(hit.get("score") or 0.0)
            if score >= record["score"]:
                record["score"] = score
                record["excerpt"] = hit.get("content")
        ordered = sorted(sessions.values(), key=lambda row: (-row["score"], -(row["event_count"]), row["last_seen_at"] or ""))[:limit]
        for row in ordered:
            row.pop("score", None)
        return {"query": query, "sessions": ordered}

    def digest(self, *, days: int = 7) -> dict[str, Any]:
        self.ensure_ready()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.engine() as engine:
            sessions = self._rows(
                engine,
                "SELECT session_id, agent, project_id, COUNT(*) AS event_count, "
                "MIN(ts) AS started_at, MAX(ts) AS last_seen_at "
                f"FROM events WHERE ts >= {_quote(cutoff)} "
                "GROUP BY session_id, agent, project_id ORDER BY last_seen_at DESC",
            )
            recent_event_ids = set(self._event_ids(engine, since=cutoff))
            touch_rows = self._rows(engine, "SELECT event_id, path FROM file_touches")
        counts: dict[str, int] = {}
        for row in touch_rows:
            if row.get("event_id") not in recent_event_ids:
                continue
            path_value = str(row.get("path") or "")
            counts[path_value] = counts.get(path_value, 0) + 1
        top_files = [
            {"path": path_value, "touches": touches}
            for path_value, touches in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ]
        return {"days": days, "since": cutoff, "sessions": sessions, "top_files": top_files}

    def sql(self, sql: str) -> dict[str, Any]:
        _assert_read_only(sql)
        self.ensure_ready()
        with self.engine() as engine:
            rows = self._rows(engine, sql)
        return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}

    def _is_stale(self) -> bool:
        if not self.raw_db_path.exists():
            return False
        if not self.sidecar_path.exists():
            return True
        return self.sidecar_path.stat().st_mtime < self.raw_db_path.stat().st_mtime

    def _engine_class(self):
        ensure_repo_on_syspath(self.repo_root)
        return import_uqa_engine(self.repo_root)

    def engine(self):
        engine_cls = self._engine_class()
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        return _EngineContext(engine_cls(db_path=str(self.sidecar_path), vector_dimensions=64))

    def _read_raw_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        with closing(sqlite3.connect(self.raw_db_path)) as db:
            db.row_factory = sqlite3.Row
            sessions = [dict(row) for row in db.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()]
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY ts, id").fetchall()]
            file_touches = [dict(row) for row in db.execute("SELECT * FROM file_touches ORDER BY event_id, path").fetchall()]
        return sessions, events, file_touches

    def _event_ids(self, engine: Any, *, project_id: str | None = None, since: str | None = None) -> list[str]:
        clauses = []
        if project_id is not None:
            clauses.append(f"project_id = {_quote(project_id)}")
        if since is not None:
            clauses.append(f"ts >= {_quote(since)}")
        sql = "SELECT id FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [str(row.get("id")) for row in self._rows(engine, sql)]

    def _events_by_ids(self, engine: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = sorted({value for value in ids if value})
        if not unique_ids:
            return {}
        clauses = " OR ".join(f"id = {_quote(value)}" for value in unique_ids)
        rows = self._rows(engine, "SELECT id, session_id, ts, kind, content FROM events WHERE " + clauses)
        return {str(row.get("id")): row for row in rows}

    def _insert_rows(self, engine: Any, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = list(rows[0].keys())
        batch_size = 200
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            values = []
            for row in batch:
                values.append("(" + ", ".join(_quote(row.get(column)) for column in columns) + ")")
            engine.sql(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES " + ", ".join(values)
            )

    def _rows(self, engine: Any, sql: str) -> list[dict[str, Any]]:
        result = engine.sql(sql)
        return [dict(row) for row in result.rows]

    def _one(self, engine: Any, sql: str) -> dict[str, Any] | None:
        rows = self._rows(engine, sql)
        return rows[0] if rows else None

    def _scalar(self, engine: Any, sql: str) -> int:
        row = self._one(engine, sql)
        if row is None:
            return 0
        value = next(iter(row.values()))
        return int(value or 0)

    def _normalize_search_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "session_id": row.get("session_id"),
            "ts": row.get("ts"),
            "kind": row.get("kind"),
            "content": row.get("content"),
            "target_path": row.get("target_path"),
            "score": float(row.get("_score") or 0.0),
        }


class _EngineContext:
    def __init__(self, engine: Any):
        self.engine = engine

    def __enter__(self) -> Any:
        return self.engine

    def __exit__(self, exc_type, exc, tb) -> None:
        self.engine.close()


def _assert_read_only(sql: str) -> None:
    stripped = sql.strip().lower()
    if not stripped.startswith(("select", "with", "explain", "pragma")):
        raise ValueError("Only read-only SQL is allowed")


def _quote(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _like(value: str) -> str:
    return f"%{value}%"
