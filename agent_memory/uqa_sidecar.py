from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
from contextlib import closing
from typing import Any

from .local_imports import optional_import


@dataclass(slots=True)
class UQABridgeStatus:
    available: bool
    reason: str | None
    sidecar_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UQASidecar:
    def __init__(self, raw_db_path: str | Path, sidecar_path: str | Path | None = None):
        self.raw_db_path = Path(raw_db_path)
        self.sidecar_path = Path(sidecar_path or self.raw_db_path.with_suffix('.uqa.db'))

    def status(self) -> dict[str, Any]:
        if self._engine_class() is None:
            return UQABridgeStatus(
                available=False,
                reason="uqa import unavailable or dependencies missing",
                sidecar_path=str(self.sidecar_path),
            ).to_dict()
        return UQABridgeStatus(
            available=True,
            reason=None,
            sidecar_path=str(self.sidecar_path),
        ).to_dict()

    def available(self) -> bool:
        return self._engine_class() is not None

    def rebuild(self) -> dict[str, Any]:
        engine_cls = self._engine_class()
        if engine_cls is None:
            raise RuntimeError("UQA is not available in this environment")
        if self.sidecar_path.exists():
            self.sidecar_path.unlink()
        rows_sessions, rows_events, rows_touches = self._read_raw_rows()
        engine = engine_cls(db_path=str(self.sidecar_path))
        try:
            engine.sql("CREATE TABLE sessions (session_id TEXT, agent TEXT, project_id TEXT, started_at TEXT, ended_at TEXT, metadata_json TEXT)")
            engine.sql("CREATE TABLE events (id TEXT, agent TEXT, session_id TEXT, project_id TEXT, ts TEXT, kind TEXT, role TEXT, content TEXT, tool_name TEXT, target_path TEXT, payload_json TEXT)")
            engine.sql("CREATE TABLE file_touches (event_id TEXT, path TEXT, operation TEXT)")
            for row in rows_sessions:
                engine.sql(
                    "INSERT INTO sessions (session_id, agent, project_id, started_at, ended_at, metadata_json) VALUES "
                    f"({_quote(row['session_id'])}, {_quote(row['agent'])}, {_quote(row['project_id'])}, {_quote(row['started_at'])}, {_quote(row['ended_at'])}, {_quote(row['metadata_json'])})"
                )
            for row in rows_events:
                engine.sql(
                    "INSERT INTO events (id, agent, session_id, project_id, ts, kind, role, content, tool_name, target_path, payload_json) VALUES "
                    f"({_quote(row['id'])}, {_quote(row['agent'])}, {_quote(row['session_id'])}, {_quote(row['project_id'])}, {_quote(row['ts'])}, {_quote(row['kind'])}, {_quote(row['role'])}, {_quote(row['content'])}, {_quote(row['tool_name'])}, {_quote(row['target_path'])}, {_quote(row['payload_json'])})"
                )
            for row in rows_touches:
                engine.sql(
                    "INSERT INTO file_touches (event_id, path, operation) VALUES "
                    f"({_quote(row['event_id'])}, {_quote(row['path'])}, {_quote(row['operation'])})"
                )
            for table in ("sessions", "events", "file_touches"):
                try:
                    engine.sql(f"ANALYZE {table}")
                except Exception:
                    pass
        finally:
            engine.close()
        return {
            "sidecar_path": str(self.sidecar_path),
            "sessions": len(rows_sessions),
            "events": len(rows_events),
            "file_touches": len(rows_touches),
        }

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        engine_cls = self._engine_class()
        if engine_cls is None:
            return []
        if not self.sidecar_path.exists():
            self.rebuild()
        engine = engine_cls(db_path=str(self.sidecar_path))
        try:
            result = engine.sql(
                "SELECT id, session_id, ts, kind, content, target_path, _score FROM events "
                f"WHERE text_match(content, {_quote(query)}) ORDER BY _score DESC LIMIT {int(limit)}"
            )
        except Exception:
            return []
        finally:
            engine.close()
        rows = []
        for row in result.rows:
            rows.append(
                {
                    "id": row.get("id"),
                    "session_id": row.get("session_id"),
                    "ts": row.get("ts"),
                    "kind": row.get("kind"),
                    "content": row.get("content"),
                    "target_path": row.get("target_path"),
                    "score": float(row.get("_score", 0.0)),
                }
            )
        return rows

    def _engine_class(self):
        mod = optional_import("uqa", checkout_name="uqa")
        if mod is None:
            return None
        try:
            return getattr(mod, "Engine", None)
        except Exception:
            return None

    def _read_raw_rows(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
        with closing(sqlite3.connect(self.raw_db_path)) as db:
            db.row_factory = sqlite3.Row
            sessions = db.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
            events = db.execute("SELECT * FROM events ORDER BY ts, id").fetchall()
            file_touches = db.execute("SELECT * FROM file_touches ORDER BY event_id, path").fetchall()
        return list(sessions), list(events), list(file_touches)


def _quote(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    return f"'{text}'"
