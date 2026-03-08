from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .models import CanonicalEvent

if TYPE_CHECKING:
    from anamnesis.adapters.base import CaptureAdapter

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    project_id TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    role TEXT,
    content TEXT,
    tool_name TEXT,
    target_path TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS file_touches (
    event_id TEXT NOT NULL,
    path TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'touch',
    PRIMARY KEY(event_id, path),
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    source TEXT NOT NULL,
    ref TEXT,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error TEXT NOT NULL,
    raw_excerpt TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_project_ts ON events(project_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_file_touches_path ON file_touches(path);
CREATE INDEX IF NOT EXISTS idx_import_failures_agent_ts ON import_failures(agent, ts);
"""


class RawMemoryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def initialize(self) -> None:
        with closing(self.connect()) as db:
            db.executescript(SCHEMA_SQL)
            db.commit()

    def upsert_session(
        self,
        *,
        session_id: str,
        agent: str,
        project_id: str,
        started_at: str | None = None,
        ended_at: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        with closing(self.connect()) as db:
            db.execute(
                """
                INSERT INTO sessions(session_id, agent, project_id, started_at, ended_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent = excluded.agent,
                    project_id = excluded.project_id,
                    started_at = COALESCE(sessions.started_at, excluded.started_at),
                    ended_at = COALESCE(excluded.ended_at, sessions.ended_at),
                    metadata_json = excluded.metadata_json
                """,
                (
                    session_id,
                    agent,
                    project_id,
                    started_at,
                    ended_at,
                    json.dumps(metadata or {}),
                ),
            )
            db.commit()

    def append_events(self, events: Iterable[CanonicalEvent]) -> int:
        items = list(events)
        if not items:
            return 0
        self.initialize()
        with closing(self.connect()) as db:
            for event in items:
                db.execute(
                    """
                    INSERT INTO sessions(session_id, agent, project_id, started_at, metadata_json)
                    VALUES (?, ?, ?, ?, '{}')
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (event.session_id, event.agent, event.project_id, event.ts),
                )
                db.execute(
                    """
                    INSERT INTO events(
                        id, agent, session_id, project_id, ts, kind, role, content,
                        tool_name, target_path, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        agent = excluded.agent,
                        session_id = excluded.session_id,
                        project_id = excluded.project_id,
                        ts = excluded.ts,
                        kind = excluded.kind,
                        role = excluded.role,
                        content = excluded.content,
                        tool_name = excluded.tool_name,
                        target_path = excluded.target_path,
                        payload_json = excluded.payload_json
                    """,
                    (
                        event.id,
                        event.agent,
                        event.session_id,
                        event.project_id,
                        event.ts,
                        event.kind,
                        event.role,
                        event.content,
                        event.tool_name,
                        event.target_path,
                        json.dumps(event.payload),
                    ),
                )
                file_touches = list(_extract_file_touches(event))
                for path, operation in file_touches:
                    db.execute(
                        """
                        INSERT INTO file_touches(event_id, path, operation)
                        VALUES (?, ?, ?)
                        ON CONFLICT(event_id, path) DO UPDATE SET operation = excluded.operation
                        """,
                        (event.id, path, operation),
                    )
            db.commit()
        return len(items)

    def append_payloads(
        self,
        adapter: "CaptureAdapter",
        payloads: Iterable[dict],
    ) -> dict[str, int]:
        normalized: list[CanonicalEvent] = []
        payload_count = 0
        for payload in payloads:
            payload_count += 1
            normalized.extend(adapter.normalize(dict(payload)))
        written = self.append_events(normalized)
        return {
            "payloads": payload_count,
            "events": written,
        }

    def record_import_failure(
        self,
        *,
        agent: str,
        source: str,
        ref: str | None,
        error: str,
        raw_excerpt: str | None = None,
    ) -> None:
        self.initialize()
        with closing(self.connect()) as db:
            db.execute(
                """
                INSERT INTO import_failures(agent, source, ref, error, raw_excerpt)
                VALUES (?, ?, ?, ?, ?)
                """,
                (agent, source, ref, error, raw_excerpt),
            )
            db.commit()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        self.initialize()
        with closing(self.connect()) as db:
            return db.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        self.initialize()
        with closing(self.connect()) as db:
            return db.execute(sql, params).fetchone()


def _extract_file_touches(event: CanonicalEvent) -> list[tuple[str, str]]:
    touches: list[tuple[str, str]] = []
    if event.target_path:
        touches.append((event.target_path, "touch"))
    raw = event.payload.get("file_touches")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                touches.append((item, "touch"))
            elif isinstance(item, dict) and item.get("path"):
                touches.append((str(item["path"]), str(item.get("operation", "touch"))))
    deduped: dict[str, str] = {}
    for path, operation in touches:
        deduped[path] = operation
    return list(deduped.items())
