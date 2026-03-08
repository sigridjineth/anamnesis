from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from agent_memory.discovery import add_repo_to_syspath, default_flex_cell_path
from agent_memory.models import RelationSchema, SchemaSummary, SearchHit

_READ_ONLY_START = ("select", "with", "pragma", "explain")
_BLOCKED = re.compile(r"\b(insert|update|delete|drop|alter|attach|detach|create|replace|vacuum|reindex|begin|commit|rollback)\b", re.IGNORECASE)


def _non_empty_statements(sql: str) -> list[str]:
    return [chunk.strip() for chunk in sql.split(';') if chunk.strip()]


def guard_read_only(sql: str) -> None:
    statements = _non_empty_statements(sql)
    if len(statements) != 1:
        raise ValueError("Exactly one read-only SQL statement is allowed")
    normalized = statements[0].lstrip().lower()
    if not normalized.startswith(_READ_ONLY_START):
        raise ValueError("Only SELECT/WITH/PRAGMA/EXPLAIN queries are allowed")
    if _BLOCKED.search(normalized):
        raise ValueError("Mutating SQL is not allowed through the shared interface")


class FlexCellProvider:
    def __init__(self, cell_name: str = "claude_code", cell_path: Path | None = None, repo_root: Path | None = None):
        self.cell_name = cell_name
        self.explicit_cell_path = cell_path
        self.repo_root = repo_root

    def resolve_cell_path(self) -> Path:
        if self.explicit_cell_path is not None:
            return self.explicit_cell_path
        if self.repo_root is not None:
            add_repo_to_syspath(self.repo_root)
            try:
                from flex.registry import resolve_cell
            except Exception:
                resolve_cell = None
            if resolve_cell is not None:
                resolved = resolve_cell(self.cell_name)
                if resolved is not None:
                    return Path(resolved)
        fallback = default_flex_cell_path(self.cell_name)
        return fallback

    def connect(self) -> sqlite3.Connection:
        cell_path = self.resolve_cell_path()
        if not cell_path.exists():
            raise FileNotFoundError(f"Flex cell not found: {cell_path}")
        db = sqlite3.connect(str(cell_path), timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _relation_columns(self, db: sqlite3.Connection, name: str) -> list[str]:
        return [row[1] for row in db.execute(f"PRAGMA table_info([{name}])").fetchall()]

    def _relations(self, db: sqlite3.Connection) -> list[RelationSchema]:
        rows = db.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [RelationSchema(name=row[0], kind=row[1], columns=self._relation_columns(db, row[0])) for row in rows]

    def orient(self) -> SchemaSummary:
        with closing(self.connect()) as db:
            relations = self._relations(db)
            metadata: dict[str, Any] = {}
            try:
                metadata = {row[0]: row[1] for row in db.execute("SELECT key, value FROM _meta ORDER BY key").fetchall()}
            except sqlite3.OperationalError:
                metadata = {}
            presets = ["@orient", "@digest", "@file", "@story", "@health"]
            return SchemaSummary(
                cell_path=str(self.resolve_cell_path()),
                relations=relations,
                metadata=metadata,
                presets=presets,
            )

    def execute_readonly(self, sql: str, limit: int | None = None) -> list[dict[str, Any]]:
        guard_read_only(sql)
        with closing(self.connect()) as db:
            rows = [dict(row) for row in db.execute(sql).fetchall()]
        return rows[:limit] if limit is not None else rows

    def _find_relation(self, db: sqlite3.Connection, *candidates: str) -> str | None:
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()}
        for candidate in candidates:
            if candidate in existing:
                return candidate
        return None

    def _projection(self, relation: str, columns: set[str], *, content_candidates: tuple[str, ...], path_candidates: tuple[str, ...]) -> str:
        def choose(candidates: tuple[str, ...]) -> str | None:
            for candidate in candidates:
                if candidate in columns:
                    return candidate
            return None

        content = choose(content_candidates)
        path = choose(path_candidates)
        parts = [
            "CAST(id AS TEXT) AS id" if "id" in columns else "CAST(rowid AS TEXT) AS id",
            f"CAST(session_id AS TEXT) AS session_id" if "session_id" in columns else "NULL AS session_id",
            f"CAST(project AS TEXT) AS project" if "project" in columns else "NULL AS project",
            f"CAST(created_at AS TEXT) AS created_at" if "created_at" in columns else "NULL AS created_at",
            f"CAST(type AS TEXT) AS type" if "type" in columns else "NULL AS type",
            f"CAST(role AS TEXT) AS role" if "role" in columns else "NULL AS role",
            f"CAST(tool_name AS TEXT) AS tool_name" if "tool_name" in columns else "NULL AS tool_name",
            f"CAST({path} AS TEXT) AS path" if path else "NULL AS path",
            f"CAST({content} AS TEXT) AS content" if content else "NULL AS content",
        ]
        return f"SELECT {', '.join(parts)} FROM [{relation}]"

    def fetch_sync_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            relation = self._find_relation(db, "messages", "chunks", "_raw_chunks")
            if relation is None:
                raise ValueError("No message-like relation found in Flex cell")
            columns = set(self._relation_columns(db, relation))
            sql = self._projection(
                relation,
                columns,
                content_candidates=("content", "text", "message", "body", "snippet"),
                path_candidates=("path", "file_path", "target_path", "source_path"),
            )
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            return [dict(row) for row in db.execute(sql).fetchall()]

    def search_like(self, query: str, limit: int = 10) -> list[SearchHit]:
        pattern = f"%{query.lower()}%"
        records = self.fetch_sync_records()
        hits: list[SearchHit] = []
        for row in records:
            haystacks = [
                str(row.get("content") or "").lower(),
                str(row.get("path") or "").lower(),
                str(row.get("tool_name") or "").lower(),
            ]
            if any(pattern.strip('%') in haystack for haystack in haystacks):
                hits.append(
                    SearchHit(
                        id=str(row.get("id")),
                        score=None,
                        session_id=row.get("session_id"),
                        project=row.get("project"),
                        created_at=row.get("created_at"),
                        type=row.get("type"),
                        tool_name=row.get("tool_name"),
                        path=row.get("path"),
                        content=row.get("content"),
                        raw=row,
                    )
                )
            if len(hits) >= limit:
                break
        return hits

    def trace_file(self, path: str, limit: int = 20) -> list[SearchHit]:
        needle = path.lower()
        hits: list[SearchHit] = []
        for row in self.fetch_sync_records():
            row_path = str(row.get("path") or "").lower()
            content = str(row.get("content") or "").lower()
            if needle in row_path or needle in content:
                hits.append(
                    SearchHit(
                        id=str(row.get("id")),
                        score=None,
                        session_id=row.get("session_id"),
                        project=row.get("project"),
                        created_at=row.get("created_at"),
                        type=row.get("type"),
                        tool_name=row.get("tool_name"),
                        path=row.get("path"),
                        content=row.get("content"),
                        raw=row,
                    )
                )
            if len(hits) >= limit:
                break
        return hits

    def digest(self, days: int = 7) -> dict[str, Any]:
        with closing(self.connect()) as db:
            summary: dict[str, Any] = {
                "days": days,
                "cell_path": str(self.resolve_cell_path()),
            }
            relation_names = {relation.name for relation in self._relations(db)}
            if "sessions" in relation_names:
                summary["sessions"] = [
                    dict(row) for row in db.execute(
                        "SELECT project, COUNT(*) AS sessions FROM sessions GROUP BY project ORDER BY sessions DESC LIMIT 10"
                    ).fetchall()
                ]
            relation = self._find_relation(db, "messages", "chunks", "_raw_chunks")
            if relation is not None:
                columns = set(self._relation_columns(db, relation))
                if "tool_name" in columns:
                    summary["tools"] = [
                        dict(row) for row in db.execute(
                            f"SELECT tool_name, COUNT(*) AS uses FROM [{relation}] WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY uses DESC LIMIT 10"
                        ).fetchall()
                    ]
                if "type" in columns:
                    summary["types"] = [
                        dict(row) for row in db.execute(
                            f"SELECT type, COUNT(*) AS count FROM [{relation}] GROUP BY type ORDER BY count DESC LIMIT 10"
                        ).fetchall()
                    ]
            return summary
