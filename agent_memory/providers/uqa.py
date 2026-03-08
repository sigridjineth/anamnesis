from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from agent_memory.discovery import add_repo_to_syspath
from agent_memory.models import SearchHit
from agent_memory.providers.flex import guard_read_only


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


class UQASidecarProvider:
    def __init__(self, db_path: Path, repo_root: Path | None = None):
        self.db_path = db_path
        self.repo_root = repo_root

    def status(self) -> dict[str, Any]:
        available, reason = self.available()
        return {
            "available": available,
            "reason": reason,
            "db_path": str(self.db_path),
            "exists": self.db_path.exists(),
        }

    def available(self) -> tuple[bool, str | None]:
        try:
            self._load_engine_class()
        except Exception as exc:
            return False, str(exc)
        return True, None

    def _load_engine_class(self):
        add_repo_to_syspath(self.repo_root)
        from uqa.engine import Engine
        return Engine

    @contextmanager
    def engine(self):
        Engine = self._load_engine_class()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = Engine(db_path=str(self.db_path), vector_dimensions=64)
        try:
            yield engine
        finally:
            engine.close()

    def rebuild_memory_events(self, records: Iterable[dict[str, Any]]) -> dict[str, Any]:
        rows = list(records)
        with self.engine() as engine:
            try:
                engine.sql("DROP TABLE IF EXISTS memory_events")
            except Exception:
                pass
            engine.sql(
                "CREATE TABLE memory_events ("
                "id TEXT PRIMARY KEY, "
                "session_id TEXT, "
                "project TEXT, "
                "created_at TEXT, "
                "type TEXT, "
                "role TEXT, "
                "tool_name TEXT, "
                "path TEXT, "
                "content TEXT)"
            )
            if rows:
                batch_size = 200
                for index in range(0, len(rows), batch_size):
                    batch = rows[index:index + batch_size]
                    values = []
                    for row in batch:
                        values.append(
                            "(" + ", ".join([
                                _sql_literal(row.get("id")),
                                _sql_literal(row.get("session_id")),
                                _sql_literal(row.get("project")),
                                _sql_literal(row.get("created_at")),
                                _sql_literal(row.get("type")),
                                _sql_literal(row.get("role")),
                                _sql_literal(row.get("tool_name")),
                                _sql_literal(row.get("path")),
                                _sql_literal(row.get("content")),
                            ]) + ")"
                        )
                    engine.sql(
                        "INSERT INTO memory_events (id, session_id, project, created_at, type, role, tool_name, path, content) VALUES " + ", ".join(values)
                    )
            try:
                engine.sql("ANALYZE memory_events")
            except Exception:
                pass
        return {"indexed_rows": len(rows), "db_path": str(self.db_path)}

    def query_readonly(self, sql: str) -> list[dict[str, Any]]:
        guard_read_only(sql)
        with self.engine() as engine:
            result = engine.sql(sql)
            return list(result.rows)

    def search_text(self, query: str, limit: int = 10, project: str | None = None) -> list[SearchHit]:
        clauses = [f"text_match(content, {_sql_literal(query)})"]
        if project:
            clauses.append(f"project = {_sql_literal(project)}")
        sql = (
            "SELECT id, session_id, project, created_at, type, tool_name, path, content, _score "
            "FROM memory_events WHERE " + " AND ".join(clauses) +
            f" ORDER BY _score DESC LIMIT {int(limit)}"
        )
        rows = self.query_readonly(sql)
        return [
            SearchHit(
                id=str(row.get("id")),
                score=float(row.get("_score")) if row.get("_score") is not None else None,
                session_id=row.get("session_id"),
                project=row.get("project"),
                created_at=row.get("created_at"),
                type=row.get("type"),
                tool_name=row.get("tool_name"),
                path=row.get("path"),
                content=row.get("content"),
                raw=row,
            )
            for row in rows
        ]
