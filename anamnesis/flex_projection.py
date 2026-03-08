from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any

from anamnesis.config import Settings
from anamnesis.embeddings import combine_text
from anamnesis.getflex_runtime import GetFlexRuntime
from anamnesis.uqa_sidecar import UQASidecar


FLEX_VECTOR_DIMENSIONS = 128
WARMUP_THRESHOLD = 5
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


SCHEMA_SQL = """
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS _ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER DEFAULT (strftime('%s','now')),
    operation TEXT,
    target TEXT,
    sql TEXT,
    params TEXT,
    rows_affected INTEGER,
    source TEXT
);

CREATE TABLE IF NOT EXISTS _raw_chunks (
    id TEXT PRIMARY KEY,
    content TEXT,
    embedding BLOB,
    timestamp INTEGER
);
CREATE TABLE IF NOT EXISTS _raw_sources (
    source_id TEXT PRIMARY KEY,
    project TEXT,
    title TEXT,
    summary TEXT,
    source TEXT,
    file_date TEXT,
    start_time INTEGER,
    end_time INTEGER,
    duration_minutes INTEGER,
    message_count INTEGER,
    episode_count INTEGER,
    primary_cwd TEXT,
    model TEXT,
    embedding BLOB,
    git_root TEXT
);
CREATE TABLE IF NOT EXISTS _edges_source (
    chunk_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT DEFAULT 'claude-code',
    position INTEGER
);
CREATE TABLE IF NOT EXISTS _edges_tool_ops (
    chunk_id TEXT PRIMARY KEY,
    tool_name TEXT,
    target_file TEXT,
    success INTEGER,
    cwd TEXT,
    git_branch TEXT
);
CREATE TABLE IF NOT EXISTS _types_message (
    chunk_id TEXT PRIMARY KEY,
    type TEXT,
    role TEXT,
    chunk_number INTEGER,
    parent_uuid TEXT,
    is_sidechain INTEGER,
    entry_uuid TEXT
);
CREATE TABLE IF NOT EXISTS _edges_delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT,
    child_session_id TEXT,
    agent_type TEXT,
    created_at INTEGER,
    parent_source_id TEXT
);
CREATE TABLE IF NOT EXISTS _raw_content (
    hash TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tool_name TEXT,
    byte_length INTEGER,
    first_seen INTEGER
);
CREATE TABLE IF NOT EXISTS _edges_raw_content (
    chunk_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (chunk_id, content_hash)
);
CREATE TABLE IF NOT EXISTS _edges_file_identity (
    chunk_id TEXT NOT NULL,
    file_uuid TEXT NOT NULL,
    PRIMARY KEY (chunk_id, file_uuid)
);
CREATE TABLE IF NOT EXISTS _edges_repo_identity (
    chunk_id TEXT NOT NULL,
    repo_root TEXT NOT NULL,
    is_tracked INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS _edges_content_identity (
    chunk_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    blob_hash TEXT,
    old_blob_hash TEXT
);
CREATE TABLE IF NOT EXISTS _edges_url_identity (
    chunk_id TEXT NOT NULL,
    url_uuid TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS _edges_soft_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT,
    file_path TEXT,
    file_uuid TEXT,
    inferred_op TEXT,
    confidence TEXT
);
CREATE TABLE IF NOT EXISTS _types_source_warmup (
    source_id TEXT PRIMARY KEY,
    is_warmup_only INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS _enrich_session_summary (
    source_id TEXT PRIMARY KEY,
    fingerprint_index TEXT
);
CREATE TABLE IF NOT EXISTS _enrich_source_graph (
    source_id TEXT PRIMARY KEY,
    centrality REAL,
    is_hub INTEGER DEFAULT 0,
    is_bridge INTEGER DEFAULT 0,
    community_id INTEGER,
    community_label TEXT
);
CREATE TABLE IF NOT EXISTS _enrich_repo_identity (
    repo_root TEXT PRIMARY KEY,
    repo_path TEXT,
    project TEXT,
    git_remote TEXT
);
CREATE TABLE IF NOT EXISTS _enrich_file_graph (
    source_id TEXT PRIMARY KEY,
    file_community_id INTEGER,
    file_centrality REAL,
    file_is_hub INTEGER DEFAULT 0,
    shared_file_count INTEGER
);
CREATE TABLE IF NOT EXISTS _enrich_delegation_graph (
    source_id TEXT PRIMARY KEY,
    agents_spawned INTEGER,
    is_orchestrator INTEGER DEFAULT 0,
    delegation_depth INTEGER,
    parent_session TEXT
);
CREATE TABLE IF NOT EXISTS _views (
    name TEXT PRIMARY KEY,
    sql TEXT NOT NULL,
    description TEXT,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS _presets (
    name TEXT PRIMARY KEY,
    description TEXT,
    params TEXT DEFAULT '',
    sql TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_source_source_id ON _edges_source(source_id, position);
CREATE INDEX IF NOT EXISTS idx_edges_tool_ops_target_file ON _edges_tool_ops(target_file);
CREATE INDEX IF NOT EXISTS idx_edges_file_identity_file_uuid ON _edges_file_identity(file_uuid);
CREATE INDEX IF NOT EXISTS idx_edges_repo_identity_repo_root ON _edges_repo_identity(repo_root);
CREATE INDEX IF NOT EXISTS idx_edges_content_identity_hash ON _edges_content_identity(content_hash);
CREATE INDEX IF NOT EXISTS idx_edges_url_identity_url_uuid ON _edges_url_identity(url_uuid);
CREATE INDEX IF NOT EXISTS idx_edges_delegations_parent ON _edges_delegations(parent_source_id, child_session_id);
CREATE INDEX IF NOT EXISTS idx_raw_chunks_timestamp ON _raw_chunks(timestamp);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, content='_raw_chunks', content_rowid='rowid');
CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(content, content='_raw_content', content_rowid='rowid');
"""


@dataclass(slots=True)
class FlexCellProjector:
    settings: Settings
    raw_db_path: Path
    sidecar_path: Path
    cell_name: str = "claude_code"

    @property
    def cell_path(self) -> Path:
        return self.settings.workspace_root / ".flex" / "cells" / f"{self.cell_name}.db"

    def ensure_ready(self) -> Path:
        if self._is_stale():
            self.rebuild()
        return self.cell_path

    def rebuild(self) -> dict[str, Any]:
        sidecar = UQASidecar(self.raw_db_path, self.sidecar_path, repo_root=self.settings.uqa_repo_root)
        sidecar.ensure_ready()
        self.cell_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = self.cell_path.with_name(f".{self.cell_path.name}.tmp-{os.getpid()}")
        if tmp.exists():
            tmp.unlink()
        with (
            sqlite3.connect(tmp) as out_db,
            sqlite3.connect(self.sidecar_path) as side_db,
            sqlite3.connect(self.raw_db_path) as raw_db,
        ):
            out_db.row_factory = sqlite3.Row
            side_db.row_factory = sqlite3.Row
            raw_db.row_factory = sqlite3.Row
            out_db.executescript(SCHEMA_SQL)
            sessions = self._rows(side_db, "SELECT * FROM _data_sessions ORDER BY project_id, anchor_epoch, session_id")
            events = self._rows(side_db, "SELECT * FROM _data_events ORDER BY ts_epoch, sequence, event_id")
            touches = self._rows(side_db, "SELECT * FROM _data_touch_activity ORDER BY ts_epoch, doc_id")
            files = self._rows(side_db, "SELECT * FROM _data_files ORDER BY project_id, canonical_path")
            aliases = self._rows(side_db, "SELECT * FROM _data_file_aliases ORDER BY project_id, canonical_path, path")
            links = self._rows(side_db, "SELECT * FROM _data_session_links ORDER BY ts, doc_id")
            search_docs = self._rows(side_db, "SELECT * FROM _data_search_docs ORDER BY doc_id")
            raw_events = self._rows(raw_db, "SELECT id, agent, session_id, project_id, ts, kind, role, content, tool_name, target_path, payload_json FROM events ORDER BY ts, id")
            raw_events_by_id = {
                str(row.get("id") or ""): {**row, "_payload": _json_object(row.get("payload_json"))}
                for row in raw_events
            }
            self._populate_meta(out_db, sessions, events, files)
            self._populate_sources(out_db, sessions)
            self._populate_chunks(out_db, events)
            self._populate_source_edges(out_db, events)
            self._populate_tool_ops(out_db, events)
            self._populate_message_types(out_db, events)
            self._populate_raw_content(out_db, events, raw_events_by_id)
            self._populate_file_identity(out_db, touches)
            self._populate_repo_identity(out_db, events, raw_events_by_id)
            self._populate_content_identity(out_db, events, raw_events_by_id)
            self._populate_url_identity(out_db, events, raw_events_by_id)
            self._populate_soft_ops(out_db, events, raw_events_by_id, touches)
            self._populate_delegations(out_db, links, events, sessions)
            self._populate_session_enrichments(out_db, sessions, touches, links)
            self._populate_repo_enrichments(out_db, sessions)
            self._materialize_embeddings(out_db)
            self._rebuild_fts(out_db)
            out_db.commit()

        tmp.replace(self.cell_path)
        runtime = GetFlexRuntime(self.settings.workspace_root)
        runtime.register_and_install_assets(
            cell_name=self.cell_name,
            db_path=self.cell_path,
            description="Anamnesis projected claude_code cell (UQA-backed)",
        )
        enrichment = runtime.run_claude_code_enrichment(cell_name=self.cell_name, db_path=self.cell_path)
        return {
            "cell": self.cell_name,
            "cell_path": str(self.cell_path),
            "sessions": len(sessions),
            "events": len(events),
            "touches": len(touches),
            "files": len(files),
            "aliases": len(aliases),
            "links": len(links),
            "search_docs": len(search_docs),
            "backend": "uqa->flex-projection",
            "enrichment": enrichment,
        }

    def _is_stale(self) -> bool:
        if not self.cell_path.exists():
            return True
        cell_mtime = self.cell_path.stat().st_mtime
        raw_mtime = self.raw_db_path.stat().st_mtime if self.raw_db_path.exists() else 0
        sidecar_mtime = self.sidecar_path.stat().st_mtime if self.sidecar_path.exists() else 0
        return max(raw_mtime, sidecar_mtime) > cell_mtime

    @staticmethod
    def _rows(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in db.execute(sql, params).fetchall()]

    def _populate_meta(self, db: sqlite3.Connection, sessions: list[dict[str, Any]], events: list[dict[str, Any]], files: list[dict[str, Any]]) -> None:
        description = "Anamnesis projected claude_code cell (UQA-backed)"
        retrieval = {
            "retrieval:backend": "uqa",
            "retrieval:projection": "flex-compatible",
        }
        db.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)", ("description", description))
        for key, value in retrieval.items():
            db.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)", (key, value))
        db.execute(
            "INSERT INTO _ops(operation, target, params, rows_affected, source) VALUES (?, ?, ?, ?, ?)",
            (
                "anamnesis_projection_sync",
                self.cell_name,
                json.dumps({"sessions": len(sessions), "events": len(events), "files": len(files)}),
                len(events),
                "anamnesis.flex_projection",
            ),
        )

    def _populate_sources(self, db: sqlite3.Connection, sessions: list[dict[str, Any]]) -> None:
        rows = []
        warmups = []
        summaries = []
        for row in sessions:
            summary = str(row.get("summary") or row.get("search_text") or f"Session {row['session_id']}")
            title = summary.splitlines()[0][:240] if summary else f"Session {row['session_id']}"
            start_epoch = int(row.get("started_at_epoch") or row.get("anchor_epoch") or 0)
            end_epoch = int(row.get("ended_at_epoch") or row.get("anchor_epoch") or start_epoch)
            duration_minutes = max(0, round((end_epoch - start_epoch) / 60))
            rows.append(
                (
                    row["session_id"],
                    row.get("project_id"),
                    title,
                    summary,
                    "anamnesis",
                    str(row.get("started_at") or row.get("anchor_ts") or "")[:10],
                    start_epoch,
                    end_epoch,
                    duration_minutes,
                    int(row.get("event_count") or 0),
                    1 + int(row.get("child_session_count") or 0),
                    row.get("project_id"),
                    row.get("agent"),
                    None,
                    row.get("project_id"),
                )
            )
            warmups.append((row["session_id"], 1 if int(row.get("event_count") or 0) < WARMUP_THRESHOLD else 0))
            summaries.append((row["session_id"], summary[:1000]))
        db.executemany(
            "INSERT OR REPLACE INTO _raw_sources(source_id, project, title, summary, source, file_date, start_time, end_time, duration_minutes, message_count, episode_count, primary_cwd, model, embedding, git_root) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        db.executemany("INSERT OR REPLACE INTO _types_source_warmup(source_id, is_warmup_only) VALUES (?, ?)", warmups)
        db.executemany("INSERT OR REPLACE INTO _enrich_session_summary(source_id, fingerprint_index) VALUES (?, ?)", summaries)

    def _populate_chunks(self, db: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
        rows = []
        for row in events:
            content = self._chunk_content(row)
            rows.append(
                (
                    row["event_id"],
                    content,
                    None,
                    int(row.get("ts_epoch") or 0),
                )
            )
        db.executemany("INSERT OR REPLACE INTO _raw_chunks(id, content, embedding, timestamp) VALUES (?, ?, ?, ?)", rows)

    def _populate_source_edges(self, db: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
        rows = [
            (
                row["event_id"],
                row.get("session_id"),
                int(row.get("sequence") or 0),
            )
            for row in events
        ]
        db.executemany("INSERT INTO _edges_source(chunk_id, source_id, position) VALUES (?, ?, ?)", rows)

    def _populate_tool_ops(self, db: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
        rows = []
        for row in events:
            if not row.get("tool_name") and not row.get("target_path"):
                continue
            rows.append(
                (
                    row["event_id"],
                    row.get("tool_name"),
                    row.get("target_path"),
                    1,
                    row.get("project_id"),
                    None,
                )
            )
        db.executemany(
            "INSERT OR REPLACE INTO _edges_tool_ops(chunk_id, tool_name, target_file, success, cwd, git_branch) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    def _populate_message_types(self, db: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
        rows = []
        for row in events:
            kind = str(row.get("kind") or "")
            if kind == "prompt":
                msg_type = "user_prompt"
            elif kind == "assistant_message":
                msg_type = "assistant"
            elif row.get("tool_name") or kind.startswith("tool_"):
                msg_type = "tool_call"
            else:
                msg_type = kind or "message"
            rows.append(
                (
                    row["event_id"],
                    msg_type,
                    row.get("role"),
                    int(row.get("sequence") or 0),
                    row.get("call_id") or row.get("message_id"),
                    0,
                    row.get("base_event_id") or row["event_id"],
                )
            )
        db.executemany(
            "INSERT OR REPLACE INTO _types_message(chunk_id, type, role, chunk_number, parent_uuid, is_sidechain, entry_uuid) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def _populate_raw_content(
        self,
        db: sqlite3.Connection,
        events: list[dict[str, Any]],
        raw_events_by_id: dict[str, dict[str, Any]],
    ) -> None:
        content_rows = []
        edge_rows = []
        for row in events:
            raw = raw_events_by_id.get(str(row.get("event_id") or ""), {})
            for content in _content_variants(row, raw):
                digest = sha256(content.encode("utf-8")).hexdigest()
                content_rows.append(
                    (
                        digest,
                        content,
                        row.get("tool_name"),
                        len(content.encode("utf-8")),
                        int(row.get("ts_epoch") or 0),
                    )
                )
                edge_rows.append((row["event_id"], digest))
        db.executemany(
            "INSERT OR REPLACE INTO _raw_content(hash, content, tool_name, byte_length, first_seen) VALUES (?, ?, ?, ?, ?)",
            content_rows,
        )
        db.executemany(
            "INSERT OR REPLACE INTO _edges_raw_content(chunk_id, content_hash) VALUES (?, ?)",
            edge_rows,
        )

    def _populate_file_identity(self, db: sqlite3.Connection, touches: list[dict[str, Any]]) -> None:
        rows = []
        for row in touches:
            if row.get("event_id") and row.get("file_id"):
                rows.append((row["event_id"], row["file_id"]))
        db.executemany("INSERT OR REPLACE INTO _edges_file_identity(chunk_id, file_uuid) VALUES (?, ?)", rows)

    def _populate_repo_identity(
        self,
        db: sqlite3.Connection,
        events: list[dict[str, Any]],
        raw_events_by_id: dict[str, dict[str, Any]],
    ) -> None:
        rows = []
        seen: set[tuple[str, str]] = set()
        for row in events:
            event_id = str(row.get("event_id") or "")
            if not row.get("target_path"):
                continue
            project_id = str(row.get("project_id") or "")
            raw = raw_events_by_id.get(event_id, {})
            payload = raw.get("_payload") if isinstance(raw, dict) else {}
            repo_root = _first_text(
                payload.get("repo_root") if isinstance(payload, dict) else None,
                payload.get("cwd") if isinstance(payload, dict) else None,
                project_id,
            )
            if not repo_root:
                continue
            key = (event_id, repo_root)
            if key in seen:
                continue
            seen.add(key)
            rows.append((event_id, repo_root, 1))
        db.executemany(
            "INSERT INTO _edges_repo_identity(chunk_id, repo_root, is_tracked) VALUES (?, ?, ?)",
            rows,
        )

    def _populate_content_identity(
        self,
        db: sqlite3.Connection,
        events: list[dict[str, Any]],
        raw_events_by_id: dict[str, dict[str, Any]],
    ) -> None:
        rows = []
        for row in events:
            tool_name = str(row.get("tool_name") or "")
            if tool_name not in {"Write", "Edit", "MultiEdit"} or not row.get("target_path"):
                continue
            raw = raw_events_by_id.get(str(row.get("event_id") or ""), {})
            variants = _content_variants(row, raw)
            if not variants:
                continue
            newest = variants[0]
            content_hash = sha256(newest.encode("utf-8")).hexdigest()
            old_blob_hash = None
            if len(variants) > 1:
                old_blob_hash = sha256(variants[-1].encode("utf-8")).hexdigest()
            rows.append((row["event_id"], content_hash, content_hash, old_blob_hash))
        db.executemany(
            "INSERT INTO _edges_content_identity(chunk_id, content_hash, blob_hash, old_blob_hash) VALUES (?, ?, ?, ?)",
            rows,
        )

    def _populate_url_identity(
        self,
        db: sqlite3.Connection,
        events: list[dict[str, Any]],
        raw_events_by_id: dict[str, dict[str, Any]],
    ) -> None:
        rows = []
        seen: set[tuple[str, str]] = set()
        for row in events:
            if str(row.get("tool_name") or "") != "WebFetch":
                continue
            raw = raw_events_by_id.get(str(row.get("event_id") or ""), {})
            candidates = _content_variants(row, raw)
            for candidate in candidates:
                for url in URL_RE.findall(candidate):
                    url_uuid = sha1(url.lower().encode("utf-8")).hexdigest()
                    key = (str(row.get("event_id") or ""), url_uuid)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append((row["event_id"], url_uuid))
        db.executemany(
            "INSERT INTO _edges_url_identity(chunk_id, url_uuid) VALUES (?, ?)",
            rows,
        )

    def _populate_soft_ops(
        self,
        db: sqlite3.Connection,
        events: list[dict[str, Any]],
        raw_events_by_id: dict[str, dict[str, Any]],
        touches: list[dict[str, Any]],
    ) -> None:
        file_id_by_event: dict[str, str] = {}
        for touch in touches:
            event_id = str(touch.get("event_id") or "")
            if event_id and touch.get("file_id") and event_id not in file_id_by_event:
                file_id_by_event[event_id] = str(touch.get("file_id"))

        rows = []
        seen: set[tuple[str, str, str | None]] = set()
        for row in events:
            tool_name = str(row.get("tool_name") or "")
            if tool_name.lower() not in {"bash", "shell", "sh"}:
                continue
            raw = raw_events_by_id.get(str(row.get("event_id") or ""), {})
            payload = raw.get("_payload") if isinstance(raw, dict) else {}
            tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
            command = _command_text(tool_input) or str(row.get("content") or "")
            inferred_op = _infer_soft_op(command)
            target_file = _first_text(
                row.get("target_path"),
                payload.get("path") if isinstance(payload, dict) else None,
            )
            key = (str(row.get("event_id") or ""), target_file or "", inferred_op or "")
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                (
                    row["event_id"],
                    target_file,
                    file_id_by_event.get(str(row.get("event_id") or "")),
                    inferred_op,
                    "high" if inferred_op else "low",
                )
            )
        db.executemany(
            "INSERT INTO _edges_soft_ops(chunk_id, file_path, file_uuid, inferred_op, confidence) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    def _populate_delegations(
        self,
        db: sqlite3.Connection,
        links: list[dict[str, Any]],
        events: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
    ) -> None:
        session_by_id = {str(row.get("session_id")): row for row in sessions}
        first_event_for_session: dict[str, str] = {}
        for row in events:
            session_id = str(row.get("session_id") or "")
            first_event_for_session.setdefault(session_id, str(row.get("event_id") or ""))
        rows = []
        for row in links:
            parent = str(row.get("parent_session_id") or "")
            child = str(row.get("child_session_id") or "")
            if not parent or not child:
                continue
            child_agent = (session_by_id.get(child) or {}).get("agent")
            rows.append(
                (
                    first_event_for_session.get(parent) or row.get("event_id") or parent,
                    child,
                    child_agent or row.get("tool_name") or "delegate",
                    int(_iso_to_epoch(row.get("ts")) or 0),
                    parent,
                )
            )
        db.executemany(
            "INSERT INTO _edges_delegations(chunk_id, child_session_id, agent_type, created_at, parent_source_id) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    def _populate_session_enrichments(
        self,
        db: sqlite3.Connection,
        sessions: list[dict[str, Any]],
        touches: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> None:
        file_sessions: dict[str, set[str]] = defaultdict(set)
        session_files: dict[str, set[str]] = defaultdict(set)
        outgoing: dict[str, set[str]] = defaultdict(set)
        incoming: dict[str, set[str]] = defaultdict(set)
        for touch in touches:
            file_id = str(touch.get("file_id") or "")
            session_id = str(touch.get("session_id") or "")
            if file_id and session_id:
                file_sessions[file_id].add(session_id)
                session_files[session_id].add(file_id)
        for link in links:
            parent = str(link.get("parent_session_id") or "")
            child = str(link.get("child_session_id") or "")
            if parent and child:
                outgoing[parent].add(child)
                incoming[child].add(parent)

        neighbors: dict[str, set[str]] = defaultdict(set)
        for file_id, session_ids in file_sessions.items():
            if len(session_ids) < 2:
                continue
            for session_id in session_ids:
                neighbors[session_id].update(session_ids - {session_id})
        for parent, children in outgoing.items():
            neighbors[parent].update(children)
            for child in children:
                neighbors[child].add(parent)

        components = _connected_components({row["session_id"] for row in sessions}, neighbors)
        component_by_session: dict[str, int] = {}
        for idx, component in enumerate(components, start=1):
            for session_id in component:
                component_by_session[session_id] = idx

        degree_values = {session_id: len(neighbors.get(session_id, set())) for session_id in component_by_session}
        max_degree = max(degree_values.values(), default=0)
        hub_cutoff = max(1, math.ceil(max_degree * 0.7)) if max_degree else 0

        source_graph_rows = []
        file_graph_rows = []
        delegation_rows = []
        sessions_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sessions:
            sessions_by_project[str(row.get("project_id") or "default-project")].append(row)

        for project_id, project_sessions in sessions_by_project.items():
            project_name = Path(project_id).name or project_id
            for session in project_sessions:
                session_id = str(session.get("session_id") or "")
                degree = degree_values.get(session_id, 0)
                component_id = component_by_session.get(session_id, 0)
                shared_files = sum(1 for file_id in session_files.get(session_id, set()) if len(file_sessions[file_id]) > 1)
                label_bits = [project_name]
                if shared_files:
                    label_bits.append(f"shared:{shared_files}")
                source_graph_rows.append(
                    (
                        session_id,
                        (degree / max_degree) if max_degree else 0.0,
                        1 if degree >= hub_cutoff and degree > 0 else 0,
                        1 if incoming.get(session_id) and outgoing.get(session_id) else 0,
                        component_id,
                        " · ".join(label_bits),
                    )
                )
                file_graph_rows.append(
                    (
                        session_id,
                        component_id,
                        float(shared_files),
                        1 if shared_files > 1 else 0,
                        shared_files,
                    )
                )
                delegation_rows.append(
                    (
                        session_id,
                        len(outgoing.get(session_id, set())),
                        1 if outgoing.get(session_id) else 0,
                        _delegation_depth(session_id, outgoing),
                        next(iter(incoming.get(session_id, [])), None),
                    )
                )

        db.executemany(
            "INSERT OR REPLACE INTO _enrich_source_graph(source_id, centrality, is_hub, is_bridge, community_id, community_label) VALUES (?, ?, ?, ?, ?, ?)",
            source_graph_rows,
        )
        db.executemany(
            "INSERT OR REPLACE INTO _enrich_file_graph(source_id, file_community_id, file_centrality, file_is_hub, shared_file_count) VALUES (?, ?, ?, ?, ?)",
            file_graph_rows,
        )
        db.executemany(
            "INSERT OR REPLACE INTO _enrich_delegation_graph(source_id, agents_spawned, is_orchestrator, delegation_depth, parent_session) VALUES (?, ?, ?, ?, ?)",
            delegation_rows,
        )

    def _populate_repo_enrichments(self, db: sqlite3.Connection, sessions: list[dict[str, Any]]) -> None:
        rows = []
        for project_id in sorted({str(row.get("project_id") or "default-project") for row in sessions}):
            rows.append((project_id, project_id, Path(project_id).name or project_id, None))
        db.executemany(
            "INSERT OR REPLACE INTO _enrich_repo_identity(repo_root, repo_path, project, git_remote) VALUES (?, ?, ?, ?)",
            rows,
        )

    def _materialize_embeddings(self, db: sqlite3.Connection) -> None:
        runtime = GetFlexRuntime(self.settings.workspace_root)

        chunk_rows = db.execute("SELECT id, content FROM _raw_chunks ORDER BY timestamp ASC, id ASC").fetchall()
        if chunk_rows:
            vectors = runtime.encode_texts([str(row["content"] or "") for row in chunk_rows], matryoshka_dim=FLEX_VECTOR_DIMENSIONS)
            db.executemany(
                "UPDATE _raw_chunks SET embedding = ? WHERE id = ?",
                [(sqlite3.Binary(vector), row["id"]) for row, vector in zip(chunk_rows, vectors, strict=True)],
            )

        source_rows = db.execute(
            "SELECT source_id, summary, title FROM _raw_sources ORDER BY start_time ASC, source_id ASC"
        ).fetchall()
        if source_rows:
            source_texts = [
                combine_text([row["title"], row["summary"]])
                for row in source_rows
            ]
            vectors = runtime.encode_texts(source_texts, matryoshka_dim=FLEX_VECTOR_DIMENSIONS)
            db.executemany(
                "UPDATE _raw_sources SET embedding = ? WHERE source_id = ?",
                [(sqlite3.Binary(vector), row["source_id"]) for row, vector in zip(source_rows, vectors, strict=True)],
            )

    @staticmethod
    def _rebuild_fts(db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        db.execute("INSERT INTO content_fts(content_fts) VALUES('rebuild')")

    @staticmethod
    def _chunk_content(event: dict[str, Any]) -> str:
        content = str(event.get("content") or "").strip()
        if content:
            return content
        tool_name = str(event.get("tool_name") or "").strip()
        target = str(event.get("target_path") or "").strip()
        fallback = combine_text([tool_name, target])
        return fallback or str(event.get("kind") or "message")



def _connected_components(nodes: set[str], neighbors: dict[str, set[str]]) -> list[set[str]]:
    remaining = set(nodes)
    components: list[set[str]] = []
    while remaining:
        start = remaining.pop()
        stack = [start]
        component = {start}
        while stack:
            node = stack.pop()
            for other in neighbors.get(node, set()):
                if other not in component:
                    component.add(other)
                    if other in remaining:
                        remaining.remove(other)
                    stack.append(other)
        components.append(component)
    return components



def _delegation_depth(root: str, outgoing: dict[str, set[str]]) -> int:
    max_depth = 0
    stack: list[tuple[str, int]] = [(root, 0)]
    seen: set[str] = set()
    while stack:
        node, depth = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        max_depth = max(max_depth, depth)
        for child in outgoing.get(node, set()):
            stack.append((child, depth + 1))
    return max_depth



def _iso_to_epoch(value: Any) -> int | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _payload_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        if value.strip():
            strings.append(value.strip())
    elif isinstance(value, dict):
        for key in ("text", "content", "prompt", "query", "url", "path", "file", "file_path", "message"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                strings.append(nested.strip())
        command = value.get("command")
        if isinstance(command, list):
            strings.append(" ".join(str(part) for part in command))
        elif isinstance(command, str) and command.strip():
            strings.append(command.strip())
        for nested in value.values():
            strings.extend(_payload_strings(nested))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_payload_strings(item))
    return strings


def _content_variants(sidecar_event: dict[str, Any], raw_event: dict[str, Any]) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        text = candidate.strip()
        if not text or text in seen:
            return
        seen.add(text)
        variants.append(text)

    add(str(sidecar_event.get("content") or ""))
    payload = raw_event.get("_payload") if isinstance(raw_event, dict) else {}
    if isinstance(payload, dict):
        for key in ("tool_input", "tool_output_preview", "result", "output", "response", "content", "message"):
            add(_first_text(payload.get(key)))
            for nested in _payload_strings(payload.get(key)):
                add(nested)
        for nested in _payload_strings(payload):
            add(nested)
    if not variants and sidecar_event.get("tool_name"):
        add(combine_text([sidecar_event.get("tool_name"), sidecar_event.get("target_path")]))
    return variants


def _command_text(value: Any) -> str | None:
    if isinstance(value, dict):
        command = value.get("command")
        if isinstance(command, list):
            return " ".join(str(part) for part in command).strip() or None
        if isinstance(command, str) and command.strip():
            return command.strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _infer_soft_op(command: str | None) -> str | None:
    if not command:
        return None
    lowered = command.lower()
    if any(token in lowered for token in (" mv ", " mv\n", " rename ", " git mv ")):
        return "rename"
    if any(token in lowered for token in (" cp ", " copy ")):
        return "copy"
    if any(token in lowered for token in (" rm ", " remove ", " unlink ")):
        return "delete"
    if any(token in lowered for token in (" sed ", " perl -pi", " python ", " bash ", " sh ")):
        return "edit"
    return "touch"
