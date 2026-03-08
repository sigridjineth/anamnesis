from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import shlex
import sqlite3
from typing import Any
from uuid import uuid4

from .config import ensure_repo_on_syspath
from .embeddings import combine_text, hash_embedding, tokenize
from .local_imports import import_uqa_engine


EMBEDDING_DIMENSIONS = 64


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
            self._graph_types()
            self._compiler_class()
        except Exception as exc:
            return False, str(exc)
        return True, None

    def health(self) -> dict[str, Any]:
        self.ensure_ready()
        raw_counts = self._raw_counts()
        with self.engine() as engine:
            counts = {
                "projects": self._scalar(engine, "SELECT COUNT(*) AS n FROM projects"),
                "sessions": self._scalar(engine, "SELECT COUNT(*) AS n FROM sessions"),
                "events": self._scalar(engine, "SELECT COUNT(*) AS n FROM events"),
                "files": self._scalar(engine, "SELECT COUNT(*) AS n FROM files"),
                "file_aliases": self._scalar(engine, "SELECT COUNT(*) AS n FROM file_aliases"),
                "file_lineage": self._scalar(engine, "SELECT COUNT(*) AS n FROM file_lineage"),
                "tool_runs": self._scalar(engine, "SELECT COUNT(*) AS n FROM tool_runs"),
                "session_links": self._scalar(engine, "SELECT COUNT(*) AS n FROM session_links"),
                "touch_activity": self._scalar(engine, "SELECT COUNT(*) AS n FROM touch_activity"),
                "search_docs": self._scalar(engine, "SELECT COUNT(*) AS n FROM search_docs"),
                "graph_edges": self._scalar(engine, "SELECT COUNT(*) AS n FROM graph_edges"),
            }
        graph_counts = self._graph_counts()
        return {
            "backend": "uqa",
            "status": self.status(),
            "raw": raw_counts,
            "sidecar": counts,
            "graph": graph_counts,
            "vectors": self._vector_count(),
            "coverage": {
                "vectorized_docs": counts["search_docs"],
                "graph_vertices": graph_counts["vertices"],
                "graph_edges": graph_counts["edges"],
            },
        }

    def rebuild(self) -> dict[str, Any]:
        if not self.raw_db_path.exists():
            raise FileNotFoundError(f"raw database does not exist: {self.raw_db_path}")
        existed_before = self.sidecar_path.exists()
        model = self._build_materialized_model(*self._read_raw_rows())
        engine_cls = self._engine_class()
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.sidecar_path.with_name(f".{self.sidecar_path.name}.tmp-{os.getpid()}-{uuid4().hex}")
        if temp_path.exists():
            temp_path.unlink()
        engine = engine_cls(db_path=str(temp_path), vector_dimensions=EMBEDDING_DIMENSIONS)
        try:
            for statement in self._schema_sql():
                engine.sql(statement)
            self._insert_rows(engine, "projects", model["projects"])
            self._insert_rows(engine, "sessions", model["sessions"])
            self._insert_rows(engine, "files", model["files"])
            self._insert_rows(engine, "file_aliases", model["file_aliases"])
            self._insert_rows(engine, "file_lineage", model["file_lineage"])
            self._insert_rows(engine, "events", model["events"])
            self._insert_rows(engine, "tool_runs", model["tool_runs"])
            self._insert_rows(engine, "session_links", model["session_links"])
            self._insert_rows(engine, "touch_activity", model["touch_activity"])
            self._insert_rows(engine, "search_docs", model["search_docs"])
            self._insert_rows(engine, "graph_edges", model["graph_edges"])
            self._materialize_vectors(engine, model["search_docs"])
            self._materialize_graph(engine, model["vertices"], model["edges"])
            for statement in (
                "ANALYZE projects",
                "ANALYZE sessions",
                "ANALYZE files",
                "ANALYZE file_aliases",
                "ANALYZE file_lineage",
                "ANALYZE events",
                "ANALYZE tool_runs",
                "ANALYZE session_links",
                "ANALYZE touch_activity",
                "ANALYZE search_docs",
                "ANALYZE graph_edges",
            ):
                try:
                    engine.sql(statement)
                except Exception:
                    pass
        finally:
            engine.close()
        temp_path.replace(self.sidecar_path)
        return {
            "raw_db_path": str(self.raw_db_path),
            "sidecar_path": str(self.sidecar_path),
            "projects": len(model["projects"]),
            "sessions": len(model["sessions"]),
            "session_summaries": len(model["sessions"]),
            "files": len(model["files"]),
            "file_aliases": len(model["file_aliases"]),
            "file_lineage": len(model["file_lineage"]),
            "events": len(model["events"]),
            "tool_runs": len(model["tool_runs"]),
            "session_links": len(model["session_links"]),
            "touch_activity": len(model["touch_activity"]),
            "search_docs": len(model["search_docs"]),
            "vectors": len(model["search_docs"]),
            "graph_edges": len(model["graph_edges"]),
            "graph": {"vertices": len(model["vertices"]), "edges": len(model["edges"])},
            "rebuild_reason": "refresh" if existed_before else "missing",
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
            filters = ["1 = 1"]
            if project_id:
                filters.append(f"project_id = {_quote(project_id)}")
            where = " AND ".join(filters)
            counts = {
                "projects": self._scalar(engine, "SELECT COUNT(*) AS n FROM projects" + (f" WHERE project_id = {_quote(project_id)}" if project_id else "")),
                "sessions": self._scalar(engine, f"SELECT COUNT(*) AS n FROM sessions WHERE {where}"),
                "events": self._scalar(engine, f"SELECT COUNT(*) AS n FROM events WHERE {where}"),
                "files": self._scalar(engine, f"SELECT COUNT(*) AS n FROM files WHERE {where}"),
                "file_aliases": self._scalar(engine, f"SELECT COUNT(*) AS n FROM file_aliases WHERE {where}"),
                "file_lineage": self._scalar(engine, f"SELECT COUNT(*) AS n FROM file_lineage WHERE {where}"),
                "tool_runs": self._scalar(engine, f"SELECT COUNT(*) AS n FROM tool_runs WHERE {where}"),
                "session_links": self._scalar(engine, f"SELECT COUNT(*) AS n FROM session_links WHERE {where}"),
                "touch_activity": self._scalar(engine, f"SELECT COUNT(*) AS n FROM touch_activity WHERE {where}"),
                "search_docs": self._scalar(engine, f"SELECT COUNT(*) AS n FROM search_docs WHERE {where}"),
                "graph_edges": self._scalar(engine, f"SELECT COUNT(*) AS n FROM graph_edges WHERE {where}"),
            }
            window = self._one(engine, f"SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM events WHERE {where}")
            by_agent = self._rows(
                engine,
                "SELECT s.agent, COUNT(*) AS event_count "
                "FROM events e JOIN sessions s ON e.session_doc_id = s.doc_id "
                + (f"WHERE e.project_id = {_quote(project_id)} " if project_id else "")
                + " GROUP BY s.agent ORDER BY event_count DESC",
            )
            objects = []
            for table_name, table in sorted(engine._tables.items()):  # noqa: SLF001 - UQA table introspection
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
            "window": window or {"first_ts": None, "last_ts": None},
            "agents": by_agent,
            "graph": self._graph_counts(),
            "vectors": self._vector_count(),
            "uqa": self.status(),
            "presets": [
                "orient",
                "search",
                "file_search",
                "trace_file",
                "trace_decision",
                "digest",
                "story",
                "sprints",
                "genealogy",
                "bridges",
                "delegation_tree",
                "health",
                "sql",
            ],
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._hybrid_search(query, limit=limit, project_id=project_id, entity_types=entity_types)

    def file_search(self, query: str, *, limit: int = 10, project_id: str | None = None) -> list[dict[str, Any]]:
        return self._hybrid_search(query, limit=limit, project_id=project_id, entity_types=["file"])

    def trace_file(self, path: str, *, limit: int = 20, project_id: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        canonical = _normalize_path(path)
        file_alias_filter = f" AND project_id = {_quote(project_id)}" if project_id else ""
        with self.engine() as engine:
            file_rows = self._rows(
                engine,
                "SELECT doc_id, file_id, path, current_path, primary_path, canonical_path, "
                "project_id, basename, extension, alias_count, rename_count, "
                "touch_count, session_count, first_seen_ts, last_seen_ts, latest_operation "
                "FROM files "
                "WHERE doc_id IN (SELECT file_doc_id FROM file_aliases "
                f"WHERE canonical_path = {_quote(canonical)}{file_alias_filter}) "
                "ORDER BY touch_count DESC, last_seen_ts DESC LIMIT 10",
            )
            touches = self._rows(
                engine,
                "WITH target_files AS ("
                "  SELECT DISTINCT file_doc_id FROM file_aliases "
                f"  WHERE canonical_path = {_quote(canonical)}{file_alias_filter}"
                ") "
                "SELECT project_id, session_id, ts, kind, path, operation, content, tool_name, session_summary "
                "FROM touch_activity "
                "WHERE file_doc_id IN (SELECT file_doc_id FROM target_files) "
                "ORDER BY ts DESC "
                f"LIMIT {int(limit)}",
            )
            related_activity = self._rows(
                engine,
                "WITH target_files AS ("
                "  SELECT DISTINCT file_doc_id FROM file_aliases "
                f"  WHERE canonical_path = {_quote(canonical)}{file_alias_filter}"
                ") "
                "SELECT file_doc_id, project_id, session_id, ts "
                "FROM touch_activity "
                "WHERE file_doc_id NOT IN (SELECT file_doc_id FROM target_files) "
                "ORDER BY ts DESC",
            )
            aliases = self._rows(
                engine,
                "SELECT project_id, path, canonical_path, is_primary, first_seen_ts, last_seen_ts "
                "FROM file_aliases "
                "WHERE file_doc_id IN (SELECT DISTINCT file_doc_id FROM file_aliases "
                f"WHERE canonical_path = {_quote(canonical)}{file_alias_filter}) "
                "ORDER BY is_primary DESC, last_seen_ts DESC, path ASC",
            )
            target_file_doc_ids = {
                int(row.get("doc_id") or 0)
                for row in file_rows
                if row.get("doc_id") is not None
            }
            target_alias_canonical_paths = {
                str(row.get("canonical_path") or "")
                for row in aliases
                if str(row.get("canonical_path") or "")
            }
            all_lineage = self._rows(
                engine,
                (
                    "SELECT file_doc_id, project_id, relation, source_path, source_canonical_path, target_path, target_canonical_path, "
                    "ts, event_id, evidence "
                    "FROM file_lineage "
                    + (f"WHERE project_id = {_quote(project_id)} " if project_id else "")
                    + "ORDER BY ts DESC, relation ASC LIMIT 200"
                ),
            )
            target_sessions = {
                (str(row.get("project_id") or "default-project"), str(row.get("session_id") or ""))
                for row in touches
                if row.get("session_id")
            }
            related_file_stats: dict[int, dict[str, Any]] = {}
            for row in related_activity:
                session_key = (
                    str(row.get("project_id") or "default-project"),
                    str(row.get("session_id") or ""),
                )
                if session_key not in target_sessions:
                    continue
                file_doc_id = int(row.get("file_doc_id") or 0)
                if not file_doc_id:
                    continue
                stat = related_file_stats.setdefault(
                    file_doc_id,
                    {"file_doc_id": file_doc_id, "touches": 0, "last_seen_at": None},
                )
                stat["touches"] += 1
                ts = row.get("ts")
                if ts and (stat["last_seen_at"] is None or str(ts) > str(stat["last_seen_at"])):
                    stat["last_seen_at"] = ts
            related_files = sorted(
                related_file_stats.values(),
                key=lambda row: (
                    int(row.get("touches") or 0),
                    str(row.get("last_seen_at") or ""),
                ),
                reverse=True,
            )[:20]
            related_file_ids = [int(row.get("file_doc_id") or 0) for row in related_files if row.get("file_doc_id")]
            related_lookup = {}
            if related_file_ids:
                related_lookup_rows = self._rows(
                    engine,
                    "SELECT doc_id, project_id, path, canonical_path FROM files WHERE "
                    + " OR ".join(f"doc_id = {int(file_id)}" for file_id in related_file_ids),
                )
                related_lookup = {int(row["doc_id"]): row for row in related_lookup_rows}
        lineage = []
        for row in all_lineage:
            source_canonical_path = str(row.get("source_canonical_path") or "")
            target_canonical_path = str(row.get("target_canonical_path") or "")
            file_doc_id = int(row.get("file_doc_id") or 0)
            if (
                file_doc_id not in target_file_doc_ids
                and source_canonical_path not in target_alias_canonical_paths
                and target_canonical_path not in target_alias_canonical_paths
            ):
                continue
            shaped = dict(row)
            if target_canonical_path in target_alias_canonical_paths:
                shaped["match_role"] = "target"
                shaped["counterpart_path"] = row.get("source_path")
                shaped["counterpart_canonical_path"] = row.get("source_canonical_path")
            elif source_canonical_path in target_alias_canonical_paths:
                shaped["match_role"] = "source"
                shaped["counterpart_path"] = row.get("target_path")
                shaped["counterpart_canonical_path"] = row.get("target_canonical_path")
            else:
                shaped["match_role"] = "file"
            lineage.append(shaped)
            if len(lineage) >= 50:
                break
        related_files = [
            {
                "project_id": related_lookup.get(int(row.get("file_doc_id") or 0), {}).get("project_id"),
                "path": related_lookup.get(int(row.get("file_doc_id") or 0), {}).get("path"),
                "canonical_path": related_lookup.get(int(row.get("file_doc_id") or 0), {}).get("canonical_path"),
                "touches": row.get("touches"),
                "last_seen_at": row.get("last_seen_at"),
            }
            for row in related_files
            if int(row.get("file_doc_id") or 0) in related_lookup
        ]
        return {
            "path": path,
            "canonical_path": canonical,
            "project_id": project_id,
            "files": file_rows,
            "aliases": aliases,
            "lineage": lineage,
            "touches": touches,
            "related_files": related_files,
        }

    def trace_decision(self, query: str, *, limit: int = 10, project_id: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        query_vector = hash_embedding(query, dimensions=EMBEDDING_DIMENSIONS)
        search_limit = max(limit * 12, 80)
        project_filter = f" AND project_id = {_quote(project_id)}" if project_id else ""
        try:
            with self.engine() as engine:
                rows = self._rows(
                    engine,
                    (
                        "WITH hits AS ("
                        "  SELECT root_doc_id, root_entity_type, session_id, project_id, ts, title, content, _score "
                        "  FROM search_docs "
                        f"  WHERE fuse_log_odds(text_match(search_text, {_quote(query)}), knn_match({int(search_limit)}), 0.5)"
                        + project_filter
                        + "), filtered AS ("
                        "  SELECT * FROM hits "
                        "  WHERE session_id IS NOT NULL AND (root_entity_type = 'event' OR root_entity_type = 'session' OR root_entity_type = 'tool_run')"
                        "), session_rank AS ("
                        "  SELECT session_id, project_id, MIN(ts) AS first_seen_at, MAX(ts) AS last_seen_at, COUNT(*) AS event_count, MAX(_score) AS top_score "
                        "  FROM filtered GROUP BY session_id, project_id"
                        "), excerpts AS ("
                        "  SELECT session_id, project_id, title, content, _score, ts, "
                        "         ROW_NUMBER() OVER (PARTITION BY session_id, project_id ORDER BY _score DESC, ts DESC) AS rn "
                        "  FROM filtered"
                        ") "
                        "SELECT s.session_id, s.project_id, s.first_seen_at, s.last_seen_at, s.event_count, s.top_score, "
                        "e.title, e.content "
                        "FROM session_rank s LEFT JOIN excerpts e "
                        "ON e.session_id = s.session_id AND e.project_id = s.project_id AND e.rn = 1 "
                        "ORDER BY s.top_score DESC, s.event_count DESC, s.last_seen_at DESC, s.project_id ASC "
                        f"LIMIT {int(limit)}"
                    ),
                    query_vector=query_vector,
                )
        except Exception:
            hits = self._hybrid_search(
                query,
                limit=max(limit * 10, 25),
                project_id=project_id,
                entity_types=["event", "session", "tool_run"],
            )
            sessions: dict[tuple[str, str], dict[str, Any]] = {}
            for hit in hits:
                session_id = str(hit.get("session_id") or "")
                hit_project_id = str(hit.get("project_id") or "default-project")
                if not session_id:
                    continue
                key = (hit_project_id, session_id)
                record = sessions.setdefault(
                    key,
                    {
                        "session_id": session_id,
                        "project_id": hit_project_id,
                        "first_seen_at": hit.get("ts"),
                        "last_seen_at": hit.get("ts"),
                        "event_count": 0,
                        "excerpt": hit.get("content") or hit.get("title"),
                        "top_score": float(hit.get("score") or 0.0),
                    },
                )
                record["event_count"] += 1
                ts = str(hit.get("ts") or "")
                if ts and (record["first_seen_at"] is None or ts < record["first_seen_at"]):
                    record["first_seen_at"] = ts
                if ts and (record["last_seen_at"] is None or ts > record["last_seen_at"]):
                    record["last_seen_at"] = ts
                score = float(hit.get("score") or 0.0)
                if score >= record["top_score"]:
                    record["top_score"] = score
                    record["excerpt"] = hit.get("content") or hit.get("title")
            rows = sorted(
                sessions.values(),
                key=lambda row: (
                    -float(row["top_score"]),
                    -int(row["event_count"]),
                    row["last_seen_at"] or "",
                    row["project_id"] or "",
                ),
            )[:limit]
            for row in rows:
                row.pop("top_score", None)
            return {"query": query, "project_id": project_id, "sessions": rows}
        sessions = []
        for row in rows:
            sessions.append(
                {
                    "session_id": row.get("session_id"),
                    "project_id": row.get("project_id"),
                    "first_seen_at": row.get("first_seen_at"),
                    "last_seen_at": row.get("last_seen_at"),
                    "event_count": row.get("event_count"),
                    "excerpt": row.get("content") or row.get("title"),
                }
            )
        return {"query": query, "project_id": project_id, "sessions": sessions}

    def digest(self, *, days: int = 7, project_id: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        project_filter = f" AND project_id = {_quote(project_id)}" if project_id else ""
        with self.engine() as engine:
            sessions = self._rows(
                engine,
                "SELECT session_id, agent, project_id, event_count, file_touch_count, started_at, ended_at, summary "
                f"FROM sessions WHERE ended_at >= {_quote(cutoff)} "
                + project_filter
                + " ORDER BY ended_at DESC, started_at DESC",
            )
            top_files = self._rows(
                engine,
                "SELECT project_id, path, canonical_path, COUNT(*) AS touches, MAX(ts) AS last_seen_at "
                f"FROM touch_activity WHERE ts >= {_quote(cutoff)} "
                + project_filter
                + " GROUP BY project_id, path, canonical_path ORDER BY touches DESC, last_seen_at DESC, project_id ASC LIMIT 10",
            )
        return {"days": days, "since": cutoff, "project_id": project_id, "sessions": sessions, "top_files": top_files}

    def story(
        self,
        query: str | None = None,
        *,
        session_id: str | None = None,
        limit: int = 50,
        context_hops: int = 2,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_ready()
        if not session_id and query:
            decision = self.trace_decision(query, limit=1, project_id=project_id)
            if decision["sessions"]:
                session_id = decision["sessions"][0]["session_id"]
                project_id = project_id or decision["sessions"][0].get("project_id")
        if not session_id:
            return {"session": None, "timeline": [], "files": [], "query": query, "project_id": project_id}
        session_filter = f" AND project_id = {_quote(project_id)}" if project_id else ""
        with self.engine() as engine:
            session_rows = self._rows(
                engine,
                "SELECT * FROM sessions "
                f"WHERE session_id = {_quote(session_id)} "
                + session_filter
                + " ORDER BY started_at DESC LIMIT 1",
            )
            resolved_project_id = project_id or (session_rows[0].get("project_id") if session_rows else None)
            event_filter = f" AND project_id = {_quote(resolved_project_id)}" if resolved_project_id else ""
            timeline = self._rows(
                engine,
                "SELECT project_id, ts, kind, role, content, tool_name, target_path "
                "FROM events "
                f"WHERE session_id = {_quote(session_id)}"
                + event_filter
                + f" ORDER BY ts ASC, sequence ASC LIMIT {int(limit)}",
            )
            files = self._rows(
                engine,
                "SELECT project_id, path, canonical_path, COUNT(*) AS touches, MAX(operation) AS operation, MAX(ts) AS last_seen_at "
                "FROM touch_activity "
                f"WHERE session_id = {_quote(session_id)} "
                + event_filter
                + " GROUP BY project_id, path, canonical_path ORDER BY touches DESC, last_seen_at DESC LIMIT 50",
            )
        if context_hops > 0 and timeline:
            timeline = timeline[: max(limit, context_hops * 10)]
        return {
            "session": session_rows[0] if session_rows else None,
            "timeline": timeline,
            "files": files,
            "query": query,
            "project_id": resolved_project_id,
        }

    def sprints(self, *, days: int = 14, project_id: str | None = None, gap_hours: int = 4) -> dict[str, Any]:
        self.ensure_ready()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        cutoff_epoch = _ts_epoch(cutoff)
        gap_seconds = gap_hours * 3600
        with self.engine() as engine:
            session_rows = self._rows(
                engine,
                "SELECT project_id, session_id, agent, started_at, ended_at, anchor_ts, anchor_epoch, event_count, file_touch_count, summary "
                "FROM sessions "
                f"WHERE anchor_epoch >= {int(cutoff_epoch)}"
                + (f" AND project_id = {_quote(project_id)}" if project_id else "")
                + " ORDER BY project_id ASC, anchor_epoch ASC, session_id ASC",
            )
        sprints: list[dict[str, Any]] = []
        current_by_project: dict[str, dict[str, Any]] = {}
        sprint_count_by_project: dict[str, int] = {}
        for row in session_rows:
            row_project_id = str(row.get("project_id") or "default-project")
            anchor_epoch = int(row.get("anchor_epoch") or 0)
            current = current_by_project.get(row_project_id)
            if current is None or anchor_epoch - int(current["last_anchor_epoch"]) > gap_seconds:
                sprint_count_by_project[row_project_id] = sprint_count_by_project.get(row_project_id, 0) + 1
                current = {
                    "project_id": row_project_id,
                    "sprint": sprint_count_by_project[row_project_id],
                    "started_at": row.get("anchor_ts") or row.get("started_at"),
                    "ended_at": row.get("anchor_ts") or row.get("ended_at") or row.get("started_at"),
                    "session_count": 0,
                    "event_count": 0,
                    "file_touch_count": 0,
                    "sessions": [],
                    "last_anchor_epoch": anchor_epoch,
                }
                current_by_project[row_project_id] = current
                sprints.append(current)
            current["session_count"] += 1
            current["event_count"] += int(row.get("event_count") or 0)
            current["file_touch_count"] += int(row.get("file_touch_count") or 0)
            current["started_at"] = min(
                str(current.get("started_at") or row.get("anchor_ts") or ""),
                str(row.get("anchor_ts") or row.get("started_at") or ""),
            )
            current["ended_at"] = max(
                str(current.get("ended_at") or row.get("anchor_ts") or ""),
                str(row.get("anchor_ts") or row.get("ended_at") or ""),
            )
            current["last_anchor_epoch"] = anchor_epoch
            current["sessions"].append(
                {
                    "session_id": row.get("session_id"),
                    "agent": row.get("agent"),
                    "started_at": row.get("started_at"),
                    "ended_at": row.get("ended_at"),
                    "summary": row.get("summary"),
                }
            )
        for sprint in sprints:
            sprint.pop("last_anchor_epoch", None)
            sprint["sessions"].sort(
                key=lambda row: (
                    str(row.get("ended_at") or row.get("started_at") or ""),
                    str(row.get("session_id") or ""),
                ),
                reverse=True,
            )
        sprints.sort(
            key=lambda row: (
                str(row.get("ended_at") or ""),
                str(row.get("project_id") or ""),
                int(row.get("sprint") or 0),
            ),
            reverse=True,
        )
        return {"days": days, "gap_hours": gap_hours, "project_id": project_id, "sprints": sprints}

    def genealogy(self, query: str, *, limit: int = 20, project_id: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        timeline = list(
            self.search(
                query,
                limit=max(limit * 2, 20),
                project_id=project_id,
                entity_types=["event", "file", "session", "tool_run"],
            )
        )
        timeline.sort(key=lambda row: (str(row.get("ts") or "9999"), -float(row.get("score") or 0.0)))
        return {"query": query, "project_id": project_id, "timeline": timeline[:limit]}

    def bridges(
        self,
        query: str,
        query_b: str | None = None,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if query_b:
            hits_a = self.search(
                query,
                limit=max(limit * 8, 20),
                project_id=project_id,
                entity_types=["event", "tool_run"],
            )
            hits_b = self.search(
                query_b,
                limit=max(limit * 8, 20),
                project_id=project_id,
                entity_types=["event", "tool_run"],
            )
            sessions_a = {str(hit.get("session_id")) for hit in hits_a if hit.get("session_id")}
            sessions_b = {str(hit.get("session_id")) for hit in hits_b if hit.get("session_id")}
            shared_sessions = sorted(sessions_a & sessions_b)
            files_a = {str(hit.get("target_path")) for hit in hits_a if hit.get("target_path")}
            files_b = {str(hit.get("target_path")) for hit in hits_b if hit.get("target_path")}
            shared_files = sorted(files_a & files_b)
            return {
                "query_a": query,
                "query_b": query_b,
                "project_id": project_id,
                "shared_sessions": shared_sessions[:limit],
                "shared_files": shared_files[:limit],
                "count_shared_sessions": len(shared_sessions),
                "count_shared_files": len(shared_files),
            }
        query_vector = hash_embedding(query, dimensions=EMBEDDING_DIMENSIONS)
        search_limit = max(limit * 12, 80)
        project_filter = f" AND project_id = {_quote(project_id)}" if project_id else ""
        with self.engine() as engine:
            bridge_rows = self._rows(
                engine,
                (
                    "WITH hits AS ("
                    "  SELECT d.project_id, d.session_id, d.target_path "
                    "  FROM ("
                    "    WITH scored AS ("
                    "      SELECT root_doc_id, MAX(_score) AS best_score "
                    "      FROM ("
                    "        SELECT root_doc_id, root_entity_type, session_id, project_id, target_path, ts, _score "
                    "        FROM search_docs "
                    f"        WHERE fuse_log_odds(text_match(search_text, {_quote(query)}), knn_match({int(search_limit)}), 0.5)"
                    + project_filter
                    + " "
                    + "      ) WHERE root_entity_type = 'event' OR root_entity_type = 'tool_run' "
                    "      GROUP BY root_doc_id"
                    "    ) "
                    "    SELECT d.project_id, d.session_id, d.target_path "
                    "    FROM scored s JOIN search_docs d ON d.doc_id = s.root_doc_id "
                    "    WHERE d.search_kind = 'root'"
                    "  ) d "
                    "  WHERE d.session_id IS NOT NULL AND d.target_path IS NOT NULL"
                    "), collapsed AS ("
                    "  SELECT project_id, target_path AS path, session_id, COUNT(*) AS events "
                    "  FROM hits GROUP BY project_id, target_path, session_id"
                    ") "
                    "SELECT project_id, path, COUNT(*) AS session_count, SUM(events) AS event_count "
                    "FROM collapsed GROUP BY project_id, path HAVING COUNT(*) > 1 "
                    "ORDER BY session_count DESC, event_count DESC, project_id ASC, path ASC "
                    f"LIMIT {int(limit)}"
                ),
                query_vector=query_vector,
            )
            bridge_sessions = self._rows(
                engine,
                (
                    "WITH hits AS ("
                    "  SELECT d.project_id, d.session_id, d.target_path "
                    "  FROM ("
                    "    WITH scored AS ("
                    "      SELECT root_doc_id, MAX(_score) AS best_score "
                    "      FROM ("
                    "        SELECT root_doc_id, root_entity_type, session_id, project_id, target_path, ts, _score "
                    "        FROM search_docs "
                    f"        WHERE fuse_log_odds(text_match(search_text, {_quote(query)}), knn_match({int(search_limit)}), 0.5)"
                    + project_filter
                    + " "
                    + "      ) WHERE root_entity_type = 'event' OR root_entity_type = 'tool_run' "
                    "      GROUP BY root_doc_id"
                    "    ) "
                    "    SELECT d.project_id, d.session_id, d.target_path "
                    "    FROM scored s JOIN search_docs d ON d.doc_id = s.root_doc_id "
                    "    WHERE d.search_kind = 'root'"
                    "  ) d "
                    "  WHERE d.session_id IS NOT NULL AND d.target_path IS NOT NULL"
                    ") "
                    "SELECT project_id, target_path AS path, session_id, COUNT(*) AS events "
                    "FROM hits GROUP BY project_id, target_path, session_id "
                    "ORDER BY project_id ASC, path ASC, events DESC, session_id ASC"
                ),
                query_vector=query_vector,
            )
        sessions_by_path: dict[tuple[str, str], list[str]] = {}
        for row in bridge_sessions:
            project = str(row.get("project_id") or "default-project")
            path = str(row.get("path") or "")
            if not path:
                continue
            sessions_by_path.setdefault((project, path), []).append(str(row.get("session_id")))
        bridges = [
            {
                "project_id": row.get("project_id"),
                "path": row.get("path"),
                "session_count": row.get("session_count"),
                "event_count": row.get("event_count"),
                "sessions": sessions_by_path.get(
                    (str(row.get("project_id") or "default-project"), str(row.get("path") or "")),
                    [],
                )[:limit],
            }
            for row in bridge_rows
        ]
        return {"query": query, "project_id": project_id, "bridges": bridges}

    def delegation_tree(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_ready()
        if not session_id and query:
            decision = self.trace_decision(query, limit=1, project_id=project_id)
            if decision["sessions"]:
                session_id = decision["sessions"][0]["session_id"]
                project_id = project_id or decision["sessions"][0].get("project_id")
        if not session_id:
            return {"sessions": [], "query": query, "project_id": project_id}
        with self.engine() as engine:
            session_row = self._one(
                engine,
                "SELECT session_id, project_id, agent, summary, started_at, anchor_ts "
                "FROM sessions WHERE session_id = "
                + _quote(session_id)
                + (f" AND project_id = {_quote(project_id)}" if project_id else ""),
            )
            if session_row is None:
                return {"sessions": [], "query": query, "project_id": project_id}
            session_project_id = str(project_id or session_row.get("project_id") or "")
            link_rows = self._rows(
                engine,
                "SELECT parent_session_id, child_session_id, label, ts, tool_name, event_id "
                "FROM session_links "
                + (f"WHERE project_id = {_quote(session_project_id)} " if session_project_id else "")
                + "ORDER BY ts ASC, parent_session_id ASC, child_session_id ASC",
            )
            children_by_parent: dict[str, list[dict[str, Any]]] = {}
            parents_by_child: dict[str, list[dict[str, Any]]] = {}
            for row in link_rows:
                parent = str(row.get("parent_session_id") or "")
                child = str(row.get("child_session_id") or "")
                if not parent or not child:
                    continue
                children_by_parent.setdefault(parent, []).append(row)
                parents_by_child.setdefault(child, []).append(row)

            descendant_depths: dict[str, int] = {}
            queue: list[tuple[str, int]] = [(session_id, 0)]
            seen_descendants = {session_id}
            while queue:
                current, depth = queue.pop(0)
                if depth >= int(limit):
                    continue
                for row in children_by_parent.get(current, []):
                    child = str(row.get("child_session_id") or "")
                    if not child:
                        continue
                    next_depth = depth + 1
                    previous_depth = descendant_depths.get(child)
                    if previous_depth is None or next_depth < previous_depth:
                        descendant_depths[child] = next_depth
                    if child not in seen_descendants:
                        seen_descendants.add(child)
                        queue.append((child, next_depth))

            ancestor_depths: dict[str, int] = {}
            queue = [(session_id, 0)]
            seen_ancestors = {session_id}
            while queue:
                current, depth = queue.pop(0)
                if depth >= int(limit):
                    continue
                for row in parents_by_child.get(current, []):
                    parent = str(row.get("parent_session_id") or "")
                    if not parent:
                        continue
                    next_depth = depth + 1
                    previous_depth = ancestor_depths.get(parent)
                    if previous_depth is None or next_depth < previous_depth:
                        ancestor_depths[parent] = next_depth
                    if parent not in seen_ancestors:
                        seen_ancestors.add(parent)
                        queue.append((parent, next_depth))

            related_session_ids = {session_id} | set(descendant_depths) | set(ancestor_depths)
            all_session_rows = self._rows(
                engine,
                (
                    "SELECT session_id, project_id, agent, summary, started_at, anchor_ts "
                    "FROM sessions "
                    + (f"WHERE project_id = {_quote(session_project_id)} " if session_project_id else "")
                    + "ORDER BY started_at ASC, anchor_ts ASC, session_id ASC"
                ),
            )
            session_rows = [
                row
                for row in all_session_rows
                if str(row.get("session_id") or "") in related_session_ids
            ]
            all_step_rows = self._rows(
                engine,
                (
                    "SELECT session_id, run_id AS base_event_id, tool_name, call_ts, result_ts, call_content, result_content, "
                    "target_path, child_session_id, file_id "
                    "FROM tool_runs "
                    + (f"WHERE project_id = {_quote(session_project_id)} " if session_project_id else "")
                    + "ORDER BY session_id ASC, call_ts ASC, result_ts ASC"
                ),
            )
            step_rows = [
                row
                for row in all_step_rows
                if str(row.get("session_id") or "") in related_session_ids
            ]

        steps_by_session: dict[str, list[dict[str, Any]]] = {}
        for step in step_rows:
            children = []
            if step.get("target_path"):
                children.append({"type": "file", "label": step.get("target_path")})
            if step.get("child_session_id"):
                children.append({"type": "session", "label": step.get("child_session_id")})
            steps_by_session.setdefault(str(step.get("session_id") or ""), []).append(
                {
                    "base_event_id": step.get("base_event_id"),
                    "tool_name": step.get("tool_name"),
                    "children": children,
                    "call": {
                        "ts": step.get("call_ts"),
                        "content": step.get("call_content"),
                        "target_path": step.get("target_path"),
                    },
                    "result": {
                        "ts": step.get("result_ts"),
                        "content": step.get("result_content"),
                    },
                }
            )

        def relation_for(candidate_session_id: str) -> str:
            if candidate_session_id == session_id:
                return "root"
            if candidate_session_id in ancestor_depths:
                return "ancestor"
            if candidate_session_id in descendant_depths:
                return "descendant"
            return "related"

        def depth_for(candidate_session_id: str) -> int:
            if candidate_session_id == session_id:
                return 0
            if candidate_session_id in ancestor_depths:
                return -int(ancestor_depths[candidate_session_id])
            if candidate_session_id in descendant_depths:
                return int(descendant_depths[candidate_session_id])
            return 0

        ordered_sessions = sorted(
            session_rows,
            key=lambda row: (
                0 if str(row.get("session_id") or "") == session_id else 1,
                0 if depth_for(str(row.get("session_id") or "")) >= 0 else 1,
                abs(depth_for(str(row.get("session_id") or ""))),
                str(row.get("started_at") or row.get("anchor_ts") or ""),
                str(row.get("session_id") or ""),
            ),
        )

        shaped_sessions = []
        for row in ordered_sessions:
            current_session_id = str(row.get("session_id") or "")
            shaped_sessions.append(
                {
                    "session_id": current_session_id,
                    "project_id": row.get("project_id"),
                    "agent": row.get("agent"),
                    "summary": row.get("summary"),
                    "relation": relation_for(current_session_id),
                    "depth": depth_for(current_session_id),
                    "steps": steps_by_session.get(current_session_id, [])[: int(limit)],
                    "children": children_by_parent.get(current_session_id, [])[: int(limit)],
                    "parents": parents_by_child.get(current_session_id, [])[: int(limit)],
                }
            )
        return {
            "root_session_id": session_id,
            "sessions": shaped_sessions,
            "query": query,
            "project_id": session_project_id or project_id,
        }

    def sql(self, sql: str) -> dict[str, Any]:
        _assert_read_only(sql)
        self.ensure_ready()
        with self.engine() as engine:
            rows = self._rows(engine, sql)
        return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}

    def _hybrid_search(
        self,
        query: str,
        *,
        limit: int,
        project_id: str | None = None,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_ready()
        query_vector = hash_embedding(query, dimensions=EMBEDDING_DIMENSIONS)
        search_limit = max(limit * 12, 80)
        try:
            with self.engine() as engine:
                rows = self._rows(
                    engine,
                    "SELECT doc_id, entity_type, external_id, session_id, project_id, ts, kind, title, content, target_path, search_kind, _score "
                    "FROM search_docs "
                    f"WHERE fuse_log_odds(text_match(search_text, {_quote(query)}), knn_match({int(search_limit)}), 0.5) "
                    "ORDER BY _score DESC, ts DESC "
                    f"LIMIT {int(search_limit)}",
                    query_vector=query_vector,
                )
        except Exception:
            rows = self._fallback_hybrid_search(
                query,
                limit=search_limit,
                project_id=project_id,
                entity_types=entity_types,
                query_vector=query_vector,
            )
            return rows[:limit]
        allowed = set(entity_types or [])
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if row.get("search_kind") != "root":
                continue
            if project_id and str(row.get("project_id") or "") != project_id:
                continue
            if allowed and str(row.get("entity_type") or "") not in allowed:
                continue
            filtered.append(row)
            if len(filtered) >= limit:
                break
        return [self._normalize_search_row(row) for row in filtered]

    def _fallback_hybrid_search(
        self,
        query: str,
        *,
        limit: int,
        project_id: str | None,
        entity_types: list[str] | None,
        query_vector: Any,
    ) -> list[dict[str, Any]]:
        text_limit = max(limit, 20)
        vector_limit = max(limit, 20)
        with self.engine() as engine:
            text_rows = self._rows(
                engine,
                "SELECT doc_id, entity_type, external_id, session_id, project_id, ts, kind, title, content, target_path, search_kind, root_doc_id, _score "
                "FROM search_docs "
                f"WHERE text_match(search_text, {_quote(query)}) ORDER BY _score DESC, ts DESC LIMIT {int(text_limit)}",
            )
            vector_rows = self._rows(
                engine,
                "SELECT doc_id, entity_type, external_id, session_id, project_id, ts, kind, title, content, target_path, search_kind, root_doc_id, _score "
                "FROM search_docs "
                f"WHERE knn_match({int(vector_limit)}) ORDER BY _score DESC, ts DESC LIMIT {int(vector_limit)}",
                query_vector=query_vector,
            )
        merged: dict[int, dict[str, Any]] = {}
        for row in text_rows:
            root_doc_id = int(row.get("root_doc_id") or row.get("doc_id") or 0)
            if row.get("search_kind") != "root":
                continue
            merged[root_doc_id] = {
                **row,
                "doc_id": root_doc_id,
                "text_score": float(row.get("_score") or 0.0),
                "vector_score": 0.0,
            }
        for row in vector_rows:
            if row.get("search_kind") != "root":
                continue
            root_doc_id = int(row.get("root_doc_id") or row.get("doc_id") or 0)
            record = merged.setdefault(
                root_doc_id,
                {
                    **row,
                    "doc_id": root_doc_id,
                    "text_score": 0.0,
                    "vector_score": 0.0,
                },
            )
            record.update({k: v for k, v in row.items() if k not in {"_score"}})
            record["doc_id"] = root_doc_id
            record["vector_score"] = float(row.get("_score") or 0.0)
        allowed = set(entity_types or [])
        ranked: list[dict[str, Any]] = []
        for row in merged.values():
            if project_id and str(row.get("project_id") or "") != project_id:
                continue
            if allowed and str(row.get("entity_type") or "") not in allowed:
                continue
            row["_score"] = row["text_score"] * 0.7 + row["vector_score"] * 0.3
            ranked.append(self._normalize_search_row(row))
        ranked.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("ts") or "")))
        return ranked

    def _is_stale(self) -> bool:
        if not self.raw_db_path.exists():
            return False
        if not self.sidecar_path.exists():
            return True
        return self.sidecar_path.stat().st_mtime < self.raw_db_path.stat().st_mtime

    def _engine_class(self):
        ensure_repo_on_syspath(self.repo_root)
        return import_uqa_engine(self.repo_root)

    def _compiler_class(self):
        ensure_repo_on_syspath(self.repo_root)
        from uqa.sql.compiler import SQLCompiler

        return SQLCompiler

    def _graph_types(self):
        ensure_repo_on_syspath(self.repo_root)
        from uqa.core.types import Edge, Vertex

        return Vertex, Edge

    def engine(self):
        engine_cls = self._engine_class()
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        return _EngineContext(engine_cls(db_path=str(self.sidecar_path), vector_dimensions=EMBEDDING_DIMENSIONS))

    def _schema_sql(self) -> tuple[str, ...]:
        return (
            "DROP TABLE IF EXISTS graph_edges",
            "DROP TABLE IF EXISTS search_docs",
            "DROP TABLE IF EXISTS session_links",
            "DROP TABLE IF EXISTS file_lineage",
            "DROP TABLE IF EXISTS file_aliases",
            "DROP TABLE IF EXISTS tool_runs",
            "DROP TABLE IF EXISTS touch_activity",
            "DROP TABLE IF EXISTS events",
            "DROP TABLE IF EXISTS files",
            "DROP TABLE IF EXISTS sessions",
            "DROP TABLE IF EXISTS projects",
            "CREATE TABLE projects (doc_id INTEGER PRIMARY KEY, project_id TEXT, title TEXT, summary TEXT, session_count INTEGER, event_count INTEGER, file_count INTEGER, first_seen_ts TEXT, last_seen_ts TEXT, search_text TEXT)",
            "CREATE TABLE sessions (doc_id INTEGER PRIMARY KEY, session_id TEXT, agent TEXT, project_id TEXT, started_at TEXT, started_at_epoch INTEGER, ended_at TEXT, ended_at_epoch INTEGER, anchor_ts TEXT, anchor_epoch INTEGER, event_count INTEGER, prompt_count INTEGER, assistant_count INTEGER, tool_event_count INTEGER, file_touch_count INTEGER, parent_session_count INTEGER, child_session_count INTEGER, summary TEXT, search_text TEXT)",
            "CREATE TABLE files (doc_id INTEGER PRIMARY KEY, file_id TEXT, project_id TEXT, path TEXT, current_path TEXT, primary_path TEXT, canonical_path TEXT, basename TEXT, extension TEXT, aliases_json TEXT, alias_count INTEGER, rename_count INTEGER, touch_count INTEGER, session_count INTEGER, first_seen_ts TEXT, first_seen_epoch INTEGER, last_seen_ts TEXT, last_seen_epoch INTEGER, latest_operation TEXT, summary TEXT, search_text TEXT)",
            "CREATE TABLE file_aliases (doc_id INTEGER PRIMARY KEY, file_doc_id INTEGER, file_id TEXT, project_id TEXT, path TEXT, canonical_path TEXT, is_primary INTEGER, first_seen_ts TEXT, last_seen_ts TEXT)",
            "CREATE TABLE file_lineage (doc_id INTEGER PRIMARY KEY, project_id TEXT, file_doc_id INTEGER, file_id TEXT, relation TEXT, source_path TEXT, source_canonical_path TEXT, target_path TEXT, target_canonical_path TEXT, event_id TEXT, ts TEXT, evidence TEXT)",
            "CREATE TABLE events (doc_id INTEGER PRIMARY KEY, event_id TEXT, base_event_id TEXT, call_id TEXT, message_id TEXT, part_id TEXT, content_hash TEXT, source TEXT, project_id TEXT, session_id TEXT, session_doc_id INTEGER, project_doc_id INTEGER, ts TEXT, ts_epoch INTEGER, sequence INTEGER, kind TEXT, role TEXT, content TEXT, tool_name TEXT, target_path TEXT, target_file_doc_id INTEGER, session_summary TEXT, search_text TEXT)",
            "CREATE TABLE tool_runs (doc_id INTEGER PRIMARY KEY, run_id TEXT, project_id TEXT, session_id TEXT, session_doc_id INTEGER, project_doc_id INTEGER, call_event_doc_id INTEGER, result_event_doc_id INTEGER, target_file_doc_id INTEGER, file_id TEXT, tool_name TEXT, call_ts TEXT, call_ts_epoch INTEGER, result_ts TEXT, result_ts_epoch INTEGER, child_session_id TEXT, call_content TEXT, result_content TEXT, target_path TEXT, summary TEXT, search_text TEXT)",
            "CREATE TABLE session_links (doc_id INTEGER PRIMARY KEY, project_id TEXT, parent_session_id TEXT, child_session_id TEXT, parent_session_doc_id INTEGER, child_session_doc_id INTEGER, label TEXT, event_id TEXT, ts TEXT, tool_name TEXT)",
            "CREATE TABLE touch_activity (doc_id INTEGER PRIMARY KEY, event_doc_id INTEGER, file_doc_id INTEGER, session_doc_id INTEGER, project_doc_id INTEGER, event_id TEXT, file_id TEXT, session_id TEXT, project_id TEXT, ts TEXT, ts_epoch INTEGER, kind TEXT, tool_name TEXT, operation TEXT, path TEXT, canonical_path TEXT, content TEXT, session_summary TEXT)",
            "CREATE TABLE search_docs (doc_id INTEGER PRIMARY KEY, root_doc_id INTEGER, root_entity_type TEXT, root_external_id TEXT, source_doc_id INTEGER, source_entity_type TEXT, entity_type TEXT, external_id TEXT, project_id TEXT, session_id TEXT, ts TEXT, kind TEXT, role TEXT, title TEXT, content TEXT, target_path TEXT, session_summary TEXT, search_kind TEXT, search_text TEXT)",
            "CREATE TABLE graph_edges (doc_id INTEGER PRIMARY KEY, edge_id TEXT, source_doc_id INTEGER, target_doc_id INTEGER, project_id TEXT, session_id TEXT, label TEXT, source_kind TEXT, target_kind TEXT, ts TEXT, properties_json TEXT)",
        )

    def _materialize_vectors(self, engine: Any, search_docs: list[dict[str, Any]]) -> None:
        for row in search_docs:
            text = row.get("search_text") or row.get("title") or row.get("content") or row.get("target_path")
            vector = hash_embedding(str(text or ""), dimensions=EMBEDDING_DIMENSIONS)
            engine.vector_index.add(int(row["doc_id"]), vector)

    def _materialize_graph(
        self,
        engine: Any,
        vertices: list[tuple[int, dict[str, Any]]],
        edges: list[tuple[int, int, int, str, dict[str, Any]]],
    ) -> None:
        Vertex, Edge = self._graph_types()
        for vertex_id, properties in vertices:
            engine.add_graph_vertex(Vertex(vertex_id=vertex_id, properties=properties))
        for edge_id, source_id, target_id, label, properties in edges:
            engine.add_graph_edge(
                Edge(
                    edge_id=edge_id,
                    source_id=source_id,
                    target_id=target_id,
                    label=label,
                    properties=properties,
                )
            )

    def _read_raw_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        with closing(sqlite3.connect(self.raw_db_path)) as db:
            db.row_factory = sqlite3.Row
            sessions = [dict(row) for row in db.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()]
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY ts, id").fetchall()]
            file_touches = [dict(row) for row in db.execute("SELECT * FROM file_touches ORDER BY event_id, path").fetchall()]
        return sessions, events, file_touches

    def _build_materialized_model(
        self,
        raw_sessions: list[dict[str, Any]],
        raw_events: list[dict[str, Any]],
        raw_touches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        allocator = _IdAllocator()
        session_meta_by_key = {
            _session_identity(row.get("project_id"), row.get("session_id")): row
            for row in raw_sessions
        }

        enriched_events: list[dict[str, Any]] = []
        lineage_hints: list[dict[str, Any]] = []
        session_link_hints: list[dict[str, Any]] = []
        extra_session_keys: set[tuple[str, str]] = set()
        project_ids_seen: set[str] = set()
        for raw_event in raw_events:
            payload = _json_object(raw_event.get("payload_json"))
            event = dict(raw_event)
            event["_payload"] = payload
            event["_source"] = _first_text(payload.get("source"), payload.get("_source"), raw_event.get("agent"), default="unknown")
            event["_call_id"] = _extract_call_id(event, payload)
            event["_message_id"] = _extract_message_id(event, payload)
            event["_part_id"] = _extract_part_id(event, payload)
            event["_content_hash"] = _sha1_text(event.get("content"))
            event["_lineage"] = _extract_lineage_hints(event, payload)
            event["_session_links"] = _extract_session_links(event, payload)
            enriched_events.append(event)
            lineage_hints.extend(event["_lineage"])
            session_link_hints.extend(event["_session_links"])
            project_ids_seen.add(str(event.get("project_id") or "default-project"))
            for link in event["_session_links"]:
                link_project_id = str(link.get("project_id") or event.get("project_id") or "default-project")
                if link.get("parent_session_id"):
                    extra_session_keys.add(_session_identity(link_project_id, link["parent_session_id"]))
                if link.get("child_session_id"):
                    extra_session_keys.add(_session_identity(link_project_id, link["child_session_id"]))

        event_by_id = {str(row.get("id")): row for row in enriched_events}

        path_meta: dict[tuple[str, str], dict[str, Any]] = {}
        touches_by_event: dict[str, list[dict[str, Any]]] = {}
        for touch in raw_touches:
            event_id = str(touch.get("event_id") or "")
            event = event_by_id.get(event_id, {})
            project_id = str(event.get("project_id") or "default-project")
            session_id = str(event.get("session_id") or "unknown-session")
            path = str(touch.get("path") or "")
            canonical_path = _normalize_path(path)
            key = (project_id, canonical_path)
            meta = path_meta.setdefault(key, _empty_path_meta(project_id=project_id, canonical_path=canonical_path))
            _record_path_observation(
                meta,
                path=path,
                operation=str(touch.get("operation") or "touch"),
                session_id=session_id,
                ts=event.get("ts"),
                content=event.get("content"),
            )
            touches_by_event.setdefault(event_id, []).append(
                {
                    "event_id": event_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "path": path,
                    "canonical_path": canonical_path,
                    "operation": str(touch.get("operation") or "touch"),
                    "path_key": key,
                }
            )

        for hint in lineage_hints:
            project_id = str(hint.get("project_id") or "default-project")
            project_ids_seen.add(project_id)
            for path, canonical_path in (
                (str(hint.get("source_path") or ""), str(hint.get("source_canonical_path") or "")),
                (str(hint.get("target_path") or ""), str(hint.get("target_canonical_path") or "")),
            ):
                if not canonical_path:
                    continue
                meta = path_meta.setdefault((project_id, canonical_path), _empty_path_meta(project_id=project_id, canonical_path=canonical_path))
                _record_path_observation(
                    meta,
                    path=path or canonical_path,
                    operation=str(hint.get("relation") or "touch"),
                    session_id=str(hint.get("session_id") or "unknown-session"),
                    ts=hint.get("ts"),
                    content=hint.get("evidence"),
                )

        union_find = _UnionFind()
        for key in path_meta:
            union_find.add(key)
        for hint in lineage_hints:
            relation = str(hint.get("relation") or "rename")
            if relation not in {"rename", "move", "git_mv"}:
                continue
            source_key = (
                str(hint.get("project_id") or "default-project"),
                str(hint.get("source_canonical_path") or ""),
            )
            target_key = (
                str(hint.get("project_id") or "default-project"),
                str(hint.get("target_canonical_path") or ""),
            )
            if source_key[1] and target_key[1]:
                union_find.union(source_key, target_key)

        component_meta: dict[tuple[str, str], dict[str, Any]] = {}
        component_for_path: dict[tuple[str, str], tuple[str, str]] = {}
        for key, meta in path_meta.items():
            root_key = union_find.find(key)
            component_for_path[key] = root_key
            component = component_meta.setdefault(root_key, _empty_component_meta(project_id=str(meta["project_id"])))
            _merge_component_meta(component, meta)

        project_ids = sorted(
            {
                str(row.get("project_id") or "default-project")
                for row in raw_sessions + enriched_events
            }
            | project_ids_seen
        )
        project_doc_ids = {project_id: allocator.next() for project_id in project_ids}
        session_keys = sorted(
            {
                _session_identity(row.get("project_id"), row.get("session_id"))
                for row in raw_sessions + enriched_events
            }
            | extra_session_keys
        )
        session_doc_ids = {session_key: allocator.next() for session_key in session_keys}
        event_doc_ids = {str(row.get("id")): allocator.next() for row in enriched_events}

        for root_key, meta in component_meta.items():
            primary_path = _best_alias_path(meta["aliases"], newest=False) or str(root_key[1])
            current_path = _best_alias_path(meta["aliases"], newest=True) or primary_path
            current_canonical = _normalize_path(current_path)
            meta["doc_id"] = allocator.next()
            meta["primary_path"] = primary_path
            meta["current_path"] = current_path
            meta["canonical_path"] = current_canonical
            meta["file_id"] = hashlib.sha1(
                f"{meta['project_id']}::{_normalize_path(primary_path)}".encode("utf-8")
            ).hexdigest()
            meta["root_key"] = root_key
        for hint in lineage_hints:
            project_id = str(hint.get("project_id") or "default-project")
            source_key = (project_id, str(hint.get("source_canonical_path") or ""))
            if source_key in component_for_path:
                component_meta[component_for_path[source_key]]["rename_count"] += 1

        session_agg: dict[tuple[str, str], dict[str, Any]] = {}
        for project_id, session_id in session_keys:
            meta = session_meta_by_key.get((project_id, session_id), {})
            session_agg[(project_id, session_id)] = {
                "doc_id": session_doc_ids[(project_id, session_id)],
                "session_id": session_id,
                "agent": str(meta.get("agent") or "unknown"),
                "project_id": project_id,
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
                "event_count": 0,
                "prompt_count": 0,
                "assistant_count": 0,
                "tool_event_count": 0,
                "file_touch_count": 0,
                "files": set(),
                "tools": [],
                "content": [],
                "parent_session_count": 0,
                "child_session_count": 0,
            }

        project_agg: dict[str, dict[str, Any]] = {
            project_id: {
                "doc_id": project_doc_ids[project_id],
                "project_id": project_id,
                "session_ids": set(),
                "file_ids": set(),
                "event_count": 0,
                "first_seen_ts": None,
                "last_seen_ts": None,
                "content": [],
            }
            for project_id in project_ids
        }

        doc_kind_by_id: dict[int, str] = {}
        projects: list[dict[str, Any]] = []
        sessions: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        file_aliases: list[dict[str, Any]] = []
        file_lineage: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        tool_runs: list[dict[str, Any]] = []
        session_links: list[dict[str, Any]] = []
        touch_activity: list[dict[str, Any]] = []
        search_docs: list[dict[str, Any]] = []
        graph_edges: list[dict[str, Any]] = []
        vertices: list[tuple[int, dict[str, Any]]] = []
        edges: list[tuple[int, int, int, str, dict[str, Any]]] = []

        sequence_by_session: dict[tuple[str, str], int] = {}
        events_grouped_by_session: dict[tuple[str, str], list[dict[str, Any]]] = {}
        links_by_event: dict[str, list[dict[str, Any]]] = {}

        for raw_event in enriched_events:
            event_id = str(raw_event.get("id") or "")
            session_id = str(raw_event.get("session_id") or "unknown-session")
            project_id = str(
                raw_event.get("project_id")
                or "default-project"
            )
            session_key = _session_identity(project_id, session_id)
            project_id = str(
                raw_event.get("project_id")
                or session_agg.get(session_key, {}).get("project_id")
                or "default-project"
            )
            session_key = _session_identity(project_id, session_id)
            session_doc_id = session_doc_ids[session_key]
            project_doc_id = project_doc_ids[project_id]
            sequence = sequence_by_session.get(session_key, 0) + 1
            sequence_by_session[session_key] = sequence
            event_touches = touches_by_event.get(event_id, [])
            paths = [str(touch.get("path") or "") for touch in event_touches]
            file_components = [
                component_meta[component_for_path[touch["path_key"]]]
                for touch in event_touches
                if touch["path_key"] in component_for_path
            ]
            file_doc_ids = [int(component["doc_id"]) for component in file_components]
            search_text = combine_text(
                [
                    str(raw_event.get("kind") or ""),
                    str(raw_event.get("role") or ""),
                    str(raw_event.get("tool_name") or ""),
                    str(raw_event.get("target_path") or ""),
                    str(raw_event.get("content") or ""),
                    " ".join(paths),
                ]
            )
            event_row = {
                "doc_id": event_doc_ids[event_id],
                "event_id": event_id,
                "base_event_id": _base_event_id(event_id),
                "call_id": raw_event.get("_call_id"),
                "message_id": raw_event.get("_message_id"),
                "part_id": raw_event.get("_part_id"),
                "content_hash": raw_event.get("_content_hash"),
                "source": raw_event.get("_source"),
                "project_id": project_id,
                "session_id": session_id,
                "session_doc_id": session_doc_id,
                "project_doc_id": project_doc_id,
                "ts": raw_event.get("ts"),
                "ts_epoch": _ts_epoch(raw_event.get("ts")),
                "sequence": sequence,
                "kind": raw_event.get("kind"),
                "role": raw_event.get("role"),
                "content": raw_event.get("content"),
                "tool_name": raw_event.get("tool_name"),
                "target_path": raw_event.get("target_path"),
                "target_file_doc_id": file_doc_ids[0] if file_doc_ids else None,
                "session_summary": None,
                "search_text": search_text,
            }
            events.append(event_row)
            events_grouped_by_session.setdefault(session_key, []).append(event_row)
            if raw_event.get("_session_links"):
                links_by_event[event_id] = list(raw_event["_session_links"])

            session_info = session_agg[session_key]
            session_info["project_id"] = project_id
            session_info["event_count"] += 1
            if raw_event.get("kind") == "prompt":
                session_info["prompt_count"] += 1
            if raw_event.get("kind") == "assistant_message":
                session_info["assistant_count"] += 1
            if raw_event.get("kind") in {"tool_call", "tool_result"}:
                session_info["tool_event_count"] += 1
            session_info["file_touch_count"] += len(event_touches)
            if raw_event.get("tool_name"):
                session_info["tools"].append(str(raw_event["tool_name"]))
            if raw_event.get("content"):
                session_info["content"].append(str(raw_event["content"]))
            for file_doc_id in file_doc_ids:
                session_info["files"].add(file_doc_id)
            if raw_event.get("ts") and (session_info["started_at"] is None or str(raw_event["ts"]) < str(session_info["started_at"])):
                session_info["started_at"] = raw_event.get("ts")
            if raw_event.get("ts") and (session_info["ended_at"] is None or str(raw_event["ts"]) > str(session_info["ended_at"])):
                session_info["ended_at"] = raw_event.get("ts")

            project_info = project_agg[project_id]
            project_info["session_ids"].add(session_id)
            project_info["file_ids"].update(file_doc_ids)
            project_info["event_count"] += 1
            if raw_event.get("content"):
                project_info["content"].append(str(raw_event["content"]))
            if raw_event.get("ts") and (project_info["first_seen_ts"] is None or str(raw_event["ts"]) < str(project_info["first_seen_ts"])):
                project_info["first_seen_ts"] = raw_event.get("ts")
            if raw_event.get("ts") and (project_info["last_seen_ts"] is None or str(raw_event["ts"]) > str(project_info["last_seen_ts"])):
                project_info["last_seen_ts"] = raw_event.get("ts")

        for link in session_link_hints:
            link_project_id = str(link.get("project_id") or "default-project")
            parent_session_id = str(link.get("parent_session_id") or "")
            child_session_id = str(link.get("child_session_id") or "")
            if not parent_session_id or not child_session_id or parent_session_id == child_session_id:
                continue
            parent_key = _session_identity(link_project_id, parent_session_id)
            child_key = _session_identity(link_project_id, child_session_id)
            if parent_key not in session_doc_ids or child_key not in session_doc_ids:
                continue
            row = {
                "doc_id": allocator.next(),
                "project_id": str(link.get("project_id") or session_agg.get(parent_key, {}).get("project_id") or "default-project"),
                "parent_session_id": parent_session_id,
                "child_session_id": child_session_id,
                "parent_session_doc_id": session_doc_ids[parent_key],
                "child_session_doc_id": session_doc_ids[child_key],
                "label": str(link.get("label") or "delegates_to"),
                "event_id": link.get("event_id"),
                "ts": link.get("ts"),
                "tool_name": link.get("tool_name"),
            }
            session_links.append(row)
            session_agg[parent_key]["child_session_count"] += 1
            session_agg[child_key]["parent_session_count"] += 1

        session_summary_by_key: dict[tuple[str, str], str] = {}
        for session_key in session_keys:
            project_id, session_id = session_key
            info = session_agg[session_key]
            file_rows = [meta for meta in component_meta.values() if int(meta["doc_id"]) in info["files"]]
            file_names = ", ".join(sorted({str(row["current_path"]) for row in file_rows})[:5])
            top_tools = ", ".join(sorted(set(info["tools"]))[:5])
            prompt_excerpt = next((value for value in info["content"] if value), None)
            summary = combine_text(
                [
                    f"Session {session_id}",
                    f"Agent: {info['agent']}",
                    prompt_excerpt,
                    f"Files: {file_names}" if file_names else None,
                    f"Tools: {top_tools}" if top_tools else None,
                ]
            )
            row = {
                "doc_id": info["doc_id"],
                "session_id": session_id,
                "agent": info["agent"],
                "project_id": info["project_id"],
                "started_at": info["started_at"],
                "started_at_epoch": _ts_epoch(info["started_at"]),
                "ended_at": info["ended_at"],
                "ended_at_epoch": _ts_epoch(info["ended_at"]),
                "anchor_ts": info["ended_at"] or info["started_at"],
                "anchor_epoch": _ts_epoch(info["ended_at"] or info["started_at"]),
                "event_count": info["event_count"],
                "prompt_count": info["prompt_count"],
                "assistant_count": info["assistant_count"],
                "tool_event_count": info["tool_event_count"],
                "file_touch_count": info["file_touch_count"],
                "parent_session_count": info["parent_session_count"],
                "child_session_count": info["child_session_count"],
                "summary": summary,
                "search_text": combine_text([summary, top_tools, file_names]),
            }
            sessions.append(row)
            session_summary_by_key[session_key] = summary
            doc_kind_by_id[int(info["doc_id"])] = "session"
            vertices.append(
                (
                    int(info["doc_id"]),
                    {
                        "kind": "session",
                        "session_id": session_id,
                        "project_id": info["project_id"],
                        "agent": info["agent"],
                        "title": session_id,
                        "summary": summary,
                        "event_count": info["event_count"],
                        "file_touch_count": info["file_touch_count"],
                    },
                )
            )

        for event_row in events:
            event_row["session_summary"] = session_summary_by_key.get(
                _session_identity(event_row.get("project_id"), event_row.get("session_id")),
                "",
            )
            doc_kind_by_id[int(event_row["doc_id"])] = "event"
            title = _short_text(
                str(event_row.get("content") or event_row.get("tool_name") or event_row.get("kind") or "event"),
                limit=120,
            )
            vertices.append(
                (
                    int(event_row["doc_id"]),
                    {
                        "kind": "event",
                        "event_id": event_row["event_id"],
                        "session_id": event_row["session_id"],
                        "project_id": event_row["project_id"],
                        "ts": event_row["ts"],
                        "role": event_row["role"],
                        "tool_name": event_row["tool_name"],
                        "target_path": event_row["target_path"],
                        "title": title,
                        "content": event_row["content"],
                    },
                )
            )

        for meta in component_meta.values():
            basename = Path(str(meta["current_path"] or meta["canonical_path"])).name
            extension = Path(basename).suffix
            aliases = sorted(meta["aliases"])
            summary = combine_text(
                [
                    str(meta["current_path"]),
                    f"Aliases: {', '.join(aliases[:8])}" if aliases else None,
                    f"Touched in {len(meta['session_ids'])} sessions",
                    _short_text("\n".join(meta["summaries"]), limit=240),
                ]
            )
            row = {
                "doc_id": int(meta["doc_id"]),
                "file_id": meta["file_id"],
                "project_id": meta["project_id"],
                "path": str(meta["current_path"]),
                "current_path": str(meta["current_path"]),
                "primary_path": str(meta["primary_path"]),
                "canonical_path": str(meta["canonical_path"]),
                "basename": basename,
                "extension": extension,
                "aliases_json": json.dumps(aliases, sort_keys=True),
                "alias_count": len(aliases),
                "rename_count": int(meta["rename_count"]),
                "touch_count": len(meta["operations"]),
                "session_count": len(meta["session_ids"]),
                "first_seen_ts": meta["first_seen_ts"],
                "first_seen_epoch": _ts_epoch(meta["first_seen_ts"]),
                "last_seen_ts": meta["last_seen_ts"],
                "last_seen_epoch": _ts_epoch(meta["last_seen_ts"]),
                "latest_operation": meta["operations"][-1] if meta["operations"] else "touch",
                "summary": summary,
                "search_text": combine_text([str(meta["current_path"]), str(meta["primary_path"]), basename, extension, " ".join(aliases), summary]),
            }
            files.append(row)
            doc_kind_by_id[int(meta["doc_id"])] = "file"
            vertices.append(
                (
                    int(meta["doc_id"]),
                    {
                        "kind": "file",
                        "file_id": meta["file_id"],
                        "project_id": meta["project_id"],
                        "path": str(meta["current_path"]),
                        "canonical_path": str(meta["canonical_path"]),
                        "title": basename or str(meta["current_path"]),
                        "summary": summary,
                        "touch_count": len(meta["operations"]),
                    },
                )
            )
            for alias_path, alias_info in sorted(meta["aliases"].items()):
                file_aliases.append(
                    {
                        "doc_id": allocator.next(),
                        "file_doc_id": meta["doc_id"],
                        "file_id": meta["file_id"],
                        "project_id": meta["project_id"],
                        "path": alias_path,
                        "canonical_path": _normalize_path(alias_path),
                        "is_primary": 1 if _normalize_path(alias_path) == _normalize_path(meta["primary_path"]) else 0,
                        "first_seen_ts": alias_info.get("first_seen_ts"),
                        "last_seen_ts": alias_info.get("last_seen_ts"),
                    }
                )

        for project_id in project_ids:
            info = project_agg[project_id]
            session_count = len(info["session_ids"])
            file_count = len(info["file_ids"])
            summary = combine_text(
                [
                    f"Project {project_id}",
                    f"Sessions: {session_count}",
                    f"Events: {info['event_count']}",
                    f"Files: {file_count}",
                    _short_text("\n".join(info["content"]), limit=240),
                ]
            )
            row = {
                "doc_id": info["doc_id"],
                "project_id": project_id,
                "title": project_id,
                "summary": summary,
                "session_count": session_count,
                "event_count": info["event_count"],
                "file_count": file_count,
                "first_seen_ts": info["first_seen_ts"],
                "last_seen_ts": info["last_seen_ts"],
                "search_text": summary,
            }
            projects.append(row)
            doc_kind_by_id[int(info["doc_id"])] = "project"
            vertices.append(
                (
                    int(info["doc_id"]),
                    {
                        "kind": "project",
                        "project_id": project_id,
                        "title": project_id,
                        "summary": summary,
                        "session_count": session_count,
                        "file_count": file_count,
                        "event_count": info["event_count"],
                    },
                )
            )

        for hint in lineage_hints:
            project_id = str(hint.get("project_id") or "default-project")
            source_key = (project_id, str(hint.get("source_canonical_path") or ""))
            if source_key not in component_for_path:
                continue
            component = component_meta[component_for_path[source_key]]
            file_lineage.append(
                {
                    "doc_id": allocator.next(),
                    "project_id": project_id,
                    "file_doc_id": component["doc_id"],
                    "file_id": component["file_id"],
                    "relation": str(hint.get("relation") or "rename"),
                    "source_path": hint.get("source_path"),
                    "source_canonical_path": hint.get("source_canonical_path"),
                    "target_path": hint.get("target_path"),
                    "target_canonical_path": hint.get("target_canonical_path"),
                    "event_id": hint.get("event_id"),
                    "ts": hint.get("ts"),
                    "evidence": hint.get("evidence"),
                }
            )

        for event_id, normalized_touches in touches_by_event.items():
            event = event_by_id.get(event_id, {})
            for normalized in normalized_touches:
                component = component_meta[component_for_path[normalized["path_key"]]]
                touch_activity.append(
                    {
                        "doc_id": allocator.next(),
                        "event_doc_id": event_doc_ids.get(event_id),
                        "file_doc_id": component["doc_id"],
                        "session_doc_id": session_doc_ids.get(_session_identity(normalized["project_id"], normalized["session_id"])),
                        "project_doc_id": project_doc_ids.get(normalized["project_id"]),
                        "event_id": event_id,
                        "file_id": component["file_id"],
                        "session_id": normalized["session_id"],
                        "project_id": normalized["project_id"],
                        "ts": event.get("ts"),
                        "ts_epoch": _ts_epoch(event.get("ts")),
                        "kind": event.get("kind"),
                        "tool_name": event.get("tool_name"),
                        "operation": normalized["operation"],
                        "path": normalized["path"],
                        "canonical_path": normalized["canonical_path"],
                        "content": event.get("content"),
                        "session_summary": session_summary_by_key.get(
                            _session_identity(normalized["project_id"], normalized["session_id"]),
                            "",
                        ),
                    }
                )

        call_docs = {row["base_event_id"]: row for row in events if row.get("kind") == "tool_call"}
        result_docs = {row["base_event_id"]: row for row in events if row.get("kind") == "tool_result"}
        child_session_by_event = {
            str(link["event_id"]): str(link["child_session_id"])
            for link in session_links
            if link.get("event_id") and link.get("child_session_id")
        }
        for base_event_id, call_row in call_docs.items():
            result_row = result_docs.get(base_event_id)
            child_session_id = child_session_by_event.get(call_row["event_id"])
            target_file_doc_id = call_row.get("target_file_doc_id") or (result_row or {}).get("target_file_doc_id")
            file_id = None
            if target_file_doc_id:
                file_id = next((row["file_id"] for row in files if int(row["doc_id"]) == int(target_file_doc_id)), None)
            summary = combine_text(
                [
                    call_row.get("tool_name"),
                    call_row.get("content"),
                    (result_row or {}).get("content"),
                    call_row.get("target_path"),
                    f"Delegates to {child_session_id}" if child_session_id else None,
                ]
            )
            row = {
                "doc_id": allocator.next(),
                "run_id": base_event_id,
                "project_id": call_row["project_id"],
                "session_id": call_row["session_id"],
                "session_doc_id": call_row["session_doc_id"],
                "project_doc_id": call_row["project_doc_id"],
                "call_event_doc_id": call_row["doc_id"],
                "result_event_doc_id": (result_row or {}).get("doc_id"),
                "target_file_doc_id": target_file_doc_id,
                "file_id": file_id,
                "tool_name": call_row.get("tool_name"),
                "call_ts": call_row.get("ts"),
                "call_ts_epoch": _ts_epoch(call_row.get("ts")),
                "result_ts": (result_row or {}).get("ts"),
                "result_ts_epoch": _ts_epoch((result_row or {}).get("ts")),
                "child_session_id": child_session_id,
                "call_content": call_row.get("content"),
                "result_content": (result_row or {}).get("content"),
                "target_path": call_row.get("target_path"),
                "summary": summary,
                "search_text": summary,
            }
            tool_runs.append(row)
            doc_kind_by_id[int(row["doc_id"])] = "tool_run"
            vertices.append(
                (
                    int(row["doc_id"]),
                    {
                        "kind": "tool_run",
                        "run_id": row["run_id"],
                        "session_id": row["session_id"],
                        "project_id": row["project_id"],
                        "tool_name": row["tool_name"],
                        "target_path": row["target_path"],
                        "child_session_id": row["child_session_id"],
                        "summary": summary,
                    },
                )
            )

        def add_search_entry(
            *,
            doc_id: int,
            entity_type: str,
            external_id: str,
            project_id: str | None,
            session_id: str | None,
            ts: str | None,
            kind: str | None,
            role: str | None,
            title: str | None,
            content: str | None,
            target_path: str | None,
            session_summary: str | None,
            search_text: str | None,
        ) -> None:
            root_row = {
                "doc_id": doc_id,
                "root_doc_id": doc_id,
                "root_entity_type": entity_type,
                "root_external_id": external_id,
                "source_doc_id": doc_id,
                "source_entity_type": entity_type,
                "entity_type": entity_type,
                "external_id": external_id,
                "project_id": project_id,
                "session_id": session_id,
                "ts": ts,
                "kind": kind,
                "role": role,
                "title": title,
                "content": content,
                "target_path": target_path,
                "session_summary": session_summary,
                "search_kind": "root",
                "search_text": search_text or combine_text([title, content, target_path]),
            }
            search_docs.append(root_row)
            chunks = _chunk_text(root_row["search_text"])
            if len(chunks) <= 1:
                return
            for index, chunk in enumerate(chunks, start=1):
                search_docs.append(
                    {
                        "doc_id": allocator.next(),
                        "root_doc_id": doc_id,
                        "root_entity_type": entity_type,
                        "root_external_id": external_id,
                        "source_doc_id": doc_id,
                        "source_entity_type": entity_type,
                        "entity_type": f"{entity_type}_chunk",
                        "external_id": f"{external_id}#chunk:{index}",
                        "project_id": project_id,
                        "session_id": session_id,
                        "ts": ts,
                        "kind": kind,
                        "role": role,
                        "title": title,
                        "content": _short_text(chunk, limit=300),
                        "target_path": target_path,
                        "session_summary": session_summary,
                        "search_kind": "chunk",
                        "search_text": chunk,
                    }
                )

        for row in projects:
            add_search_entry(
                doc_id=int(row["doc_id"]),
                entity_type="project",
                external_id=str(row["project_id"]),
                project_id=row["project_id"],
                session_id=None,
                ts=row["last_seen_ts"] or row["first_seen_ts"],
                kind="project",
                role=None,
                title=row["title"],
                content=row["summary"],
                target_path=None,
                session_summary=None,
                search_text=row["search_text"],
            )
        for row in sessions:
            add_search_entry(
                doc_id=int(row["doc_id"]),
                entity_type="session",
                external_id=str(row["session_id"]),
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["anchor_ts"],
                kind="session",
                role=None,
                title=row["session_id"],
                content=row["summary"],
                target_path=None,
                session_summary=row["summary"],
                search_text=row["search_text"],
            )
        for row in files:
            add_search_entry(
                doc_id=int(row["doc_id"]),
                entity_type="file",
                external_id=str(row["file_id"]),
                project_id=row["project_id"],
                session_id=None,
                ts=row["last_seen_ts"],
                kind="file",
                role=None,
                title=row["path"],
                content=row["summary"],
                target_path=row["path"],
                session_summary=None,
                search_text=row["search_text"],
            )
        for row in events:
            add_search_entry(
                doc_id=int(row["doc_id"]),
                entity_type="event",
                external_id=str(row["event_id"]),
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["ts"],
                kind=row["kind"],
                role=row["role"],
                title=_short_text(str(row.get("content") or row.get("tool_name") or row.get("kind") or "event"), limit=120),
                content=row["content"],
                target_path=row["target_path"],
                session_summary=row["session_summary"],
                search_text=row["search_text"],
            )
        for row in tool_runs:
            add_search_entry(
                doc_id=int(row["doc_id"]),
                entity_type="tool_run",
                external_id=str(row["run_id"]),
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["result_ts"] or row["call_ts"],
                kind="tool_run",
                role="tool",
                title=_short_text(str(row.get("tool_name") or row.get("target_path") or "tool_run"), limit=120),
                content=row["summary"],
                target_path=row["target_path"],
                session_summary=session_summary_by_key.get(
                    _session_identity(row.get("project_id"), row.get("session_id")),
                    "",
                ),
                search_text=row["search_text"],
            )

        edge_runtime_id = 1
        edge_row_id = allocator.next()

        def add_edge(
            source: int,
            target: int,
            label: str,
            *,
            project_id: str | None = None,
            session_id: str | None = None,
            ts: str | None = None,
            properties: dict[str, Any] | None = None,
        ) -> None:
            nonlocal edge_runtime_id, edge_row_id
            props = properties or {}
            source_kind = doc_kind_by_id.get(int(source), "unknown")
            target_kind = doc_kind_by_id.get(int(target), "unknown")
            graph_edges.append(
                {
                    "doc_id": edge_row_id,
                    "edge_id": f"edge-{edge_runtime_id}",
                    "source_doc_id": source,
                    "target_doc_id": target,
                    "project_id": project_id,
                    "session_id": session_id,
                    "label": label,
                    "source_kind": source_kind,
                    "target_kind": target_kind,
                    "ts": ts,
                    "properties_json": json.dumps(props, sort_keys=True),
                }
            )
            edges.append((edge_runtime_id, int(source), int(target), label, props))
            edge_runtime_id += 1
            edge_row_id += 1

        for row in sessions:
            add_edge(
                project_doc_ids[row["project_id"]],
                row["doc_id"],
                "contains_session",
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["started_at"],
            )
        for row in files:
            add_edge(
                project_doc_ids[row["project_id"]],
                row["doc_id"],
                "contains_file",
                project_id=row["project_id"],
                ts=row["last_seen_ts"],
            )
        for row in tool_runs:
            add_edge(
                row["session_doc_id"],
                row["doc_id"],
                "contains_tool_run",
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["call_ts"],
            )
            if row.get("target_file_doc_id"):
                add_edge(
                    row["doc_id"],
                    row["target_file_doc_id"],
                    "touches_file",
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    ts=row["result_ts"] or row["call_ts"],
                )
            child_session_key = _session_identity(row.get("project_id"), row.get("child_session_id"))
            if row.get("child_session_id") and child_session_key in session_doc_ids:
                add_edge(
                    row["doc_id"],
                    session_doc_ids[child_session_key],
                    "delegates_to",
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    ts=row["result_ts"] or row["call_ts"],
                )
        for session_key, rows in events_grouped_by_session.items():
            rows.sort(key=lambda item: (str(item.get("ts") or ""), int(item.get("sequence") or 0), str(item.get("event_id") or "")))
            session_doc_id = session_doc_ids[session_key]
            _, session_id = session_key
            for index, row in enumerate(rows):
                add_edge(
                    session_doc_id,
                    row["doc_id"],
                    "contains_event",
                    project_id=row["project_id"],
                    session_id=session_id,
                    ts=row["ts"],
                )
                if index > 0:
                    previous = rows[index - 1]
                    add_edge(previous["doc_id"], row["doc_id"], "next_event", project_id=row["project_id"], session_id=session_id, ts=row["ts"])
                    add_edge(row["doc_id"], previous["doc_id"], "prev_event", project_id=row["project_id"], session_id=session_id, ts=row["ts"])
        seen_session_file: set[tuple[int, int]] = set()
        for row in touch_activity:
            add_edge(
                row["event_doc_id"],
                row["file_doc_id"],
                "touches_file",
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["ts"],
                properties={"operation": row["operation"], "path": row["path"]},
            )
            add_edge(
                row["file_doc_id"],
                row["event_doc_id"],
                "touched_by",
                project_id=row["project_id"],
                session_id=row["session_id"],
                ts=row["ts"],
                properties={"operation": row["operation"], "path": row["path"]},
            )
            key = (int(row["session_doc_id"]), int(row["file_doc_id"]))
            if key not in seen_session_file:
                seen_session_file.add(key)
                add_edge(
                    row["session_doc_id"],
                    row["file_doc_id"],
                    "touches_file",
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    ts=row["ts"],
                )
                add_edge(
                    row["file_doc_id"],
                    row["session_doc_id"],
                    "appears_in_session",
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    ts=row["ts"],
                )
        for row in session_links:
            add_edge(
                row["parent_session_doc_id"],
                row["child_session_doc_id"],
                row["label"],
                project_id=row["project_id"],
                session_id=row["parent_session_id"],
                ts=row["ts"],
                properties={"tool_name": row.get("tool_name"), "event_id": row.get("event_id")},
            )
            add_edge(
                row["child_session_doc_id"],
                row["parent_session_doc_id"],
                "delegated_from",
                project_id=row["project_id"],
                session_id=row["child_session_id"],
                ts=row["ts"],
                properties={"tool_name": row.get("tool_name"), "event_id": row.get("event_id")},
            )
        for base_event_id, call_row in call_docs.items():
            result_row = result_docs.get(base_event_id)
            if not result_row:
                continue
            add_edge(
                call_row["doc_id"],
                result_row["doc_id"],
                "produces_result",
                project_id=call_row["project_id"],
                session_id=call_row["session_id"],
                ts=result_row["ts"],
            )
            add_edge(
                result_row["doc_id"],
                call_row["doc_id"],
                "result_of",
                project_id=result_row["project_id"],
                session_id=result_row["session_id"],
                ts=result_row["ts"],
            )

        return {
            "projects": projects,
            "sessions": sessions,
            "files": files,
            "file_aliases": file_aliases,
            "file_lineage": file_lineage,
            "events": events,
            "tool_runs": tool_runs,
            "session_links": session_links,
            "touch_activity": touch_activity,
            "search_docs": search_docs,
            "graph_edges": graph_edges,
            "vertices": vertices,
            "edges": edges,
        }

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
            engine.sql(f"INSERT INTO {table} ({', '.join(columns)}) VALUES " + ", ".join(values))

    def _execute(self, engine: Any, sql: str, *, query_vector: Any | None = None):
        if query_vector is None:
            return engine.sql(sql)
        compiler_cls = self._compiler_class()
        compiler = compiler_cls(engine)
        compiler.set_query_vector(query_vector)
        return compiler.execute(sql)

    def _rows(self, engine: Any, sql: str, *, query_vector: Any | None = None) -> list[dict[str, Any]]:
        result = self._execute(engine, sql, query_vector=query_vector)
        return [dict(row) for row in result.rows]

    def _one(self, engine: Any, sql: str, *, query_vector: Any | None = None) -> dict[str, Any] | None:
        rows = self._rows(engine, sql, query_vector=query_vector)
        return rows[0] if rows else None

    def _scalar(self, engine: Any, sql: str, *, query_vector: Any | None = None) -> int:
        row = self._one(engine, sql, query_vector=query_vector)
        if row is None:
            return 0
        value = next(iter(row.values()))
        return int(value or 0)

    def _normalize_search_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("external_id") or row.get("doc_id"),
            "doc_id": row.get("doc_id"),
            "entity_type": row.get("entity_type"),
            "session_id": row.get("session_id"),
            "project_id": row.get("project_id"),
            "ts": row.get("ts"),
            "kind": row.get("kind"),
            "title": row.get("title"),
            "content": row.get("content"),
            "target_path": row.get("target_path"),
            "score": float(row.get("_score") or 0.0),
        }

    def _vector_count(self) -> int:
        if not self.sidecar_path.exists():
            return 0
        with closing(sqlite3.connect(self.sidecar_path)) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_vectors'"
            ).fetchone()
            if not row or row[0] == 0:
                return 0
            return int(db.execute("SELECT COUNT(*) FROM _vectors").fetchone()[0])

    def _graph_counts(self) -> dict[str, int]:
        if not self.sidecar_path.exists():
            return {"vertices": 0, "edges": 0}
        with closing(sqlite3.connect(self.sidecar_path)) as db:
            names = {
                row[0]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            vertices = int(db.execute("SELECT COUNT(*) FROM _graph_vertices").fetchone()[0]) if "_graph_vertices" in names else 0
            edges = int(db.execute("SELECT COUNT(*) FROM _graph_edges").fetchone()[0]) if "_graph_edges" in names else 0
        return {"vertices": vertices, "edges": edges}

    def _raw_counts(self) -> dict[str, int]:
        if not self.raw_db_path.exists():
            return {"sessions": 0, "events": 0, "file_touches": 0, "import_failures": 0}
        with closing(sqlite3.connect(self.raw_db_path)) as db:
            names = {
                row[0]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            def count(name: str) -> int:
                if name not in names:
                    return 0
                return int(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            return {
                "sessions": count("sessions"),
                "events": count("events"),
                "file_touches": count("file_touches"),
                "import_failures": count("import_failures"),
            }


class _EngineContext:
    def __init__(self, engine: Any):
        self.engine = engine

    def __enter__(self) -> Any:
        return self.engine

    def __exit__(self, exc_type, exc, tb) -> None:
        self.engine.close()


class _IdAllocator:
    def __init__(self, start: int = 1):
        self.current = start

    def next(self) -> int:
        value = self.current
        self.current += 1
        return value


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}

    def add(self, item: tuple[str, str]) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: tuple[str, str]) -> tuple[str, str]:
        self.add(item)
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: tuple[str, str], right: tuple[str, str]) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if root_left <= root_right:
            self.parent[root_right] = root_left
        else:
            self.parent[root_left] = root_right


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _first_text(*values: Any, default: str | None = None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return default


def _sha1_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _empty_path_meta(*, project_id: str, canonical_path: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "canonical_path": canonical_path,
        "aliases": {},
        "operations": [],
        "session_ids": set(),
        "summaries": [],
        "first_seen_ts": None,
        "last_seen_ts": None,
    }


def _record_path_observation(
    meta: dict[str, Any],
    *,
    path: str,
    operation: str,
    session_id: str,
    ts: str | None,
    content: Any,
) -> None:
    text_path = str(path or meta["canonical_path"])
    aliases = meta.setdefault("aliases", {})
    alias = aliases.setdefault(
        text_path,
        {"first_seen_ts": ts, "last_seen_ts": ts},
    )
    if ts and (alias["first_seen_ts"] is None or str(ts) < str(alias["first_seen_ts"])):
        alias["first_seen_ts"] = ts
    if ts and (alias["last_seen_ts"] is None or str(ts) > str(alias["last_seen_ts"])):
        alias["last_seen_ts"] = ts
    meta["operations"].append(operation)
    if session_id:
        meta["session_ids"].add(session_id)
    if content:
        meta["summaries"].append(str(content))
    if ts and (meta["first_seen_ts"] is None or str(ts) < str(meta["first_seen_ts"])):
        meta["first_seen_ts"] = ts
    if ts and (meta["last_seen_ts"] is None or str(ts) > str(meta["last_seen_ts"])):
        meta["last_seen_ts"] = ts


def _empty_component_meta(*, project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "aliases": {},
        "operations": [],
        "session_ids": set(),
        "summaries": [],
        "first_seen_ts": None,
        "last_seen_ts": None,
        "rename_count": 0,
    }


def _merge_component_meta(component: dict[str, Any], meta: dict[str, Any]) -> None:
    component["project_id"] = meta["project_id"]
    for path, info in meta.get("aliases", {}).items():
        target = component["aliases"].setdefault(path, {"first_seen_ts": info.get("first_seen_ts"), "last_seen_ts": info.get("last_seen_ts")})
        first_seen_ts = info.get("first_seen_ts")
        last_seen_ts = info.get("last_seen_ts")
        if first_seen_ts and (target["first_seen_ts"] is None or str(first_seen_ts) < str(target["first_seen_ts"])):
            target["first_seen_ts"] = first_seen_ts
        if last_seen_ts and (target["last_seen_ts"] is None or str(last_seen_ts) > str(target["last_seen_ts"])):
            target["last_seen_ts"] = last_seen_ts
    component["operations"].extend(meta.get("operations", []))
    component["session_ids"].update(meta.get("session_ids", set()))
    component["summaries"].extend(meta.get("summaries", []))
    if meta.get("first_seen_ts") and (component["first_seen_ts"] is None or str(meta["first_seen_ts"]) < str(component["first_seen_ts"])):
        component["first_seen_ts"] = meta.get("first_seen_ts")
    if meta.get("last_seen_ts") and (component["last_seen_ts"] is None or str(meta["last_seen_ts"]) > str(component["last_seen_ts"])):
        component["last_seen_ts"] = meta.get("last_seen_ts")


def _best_alias_path(aliases: dict[str, dict[str, Any]], *, newest: bool) -> str | None:
    if not aliases:
        return None
    key_name = "last_seen_ts" if newest else "first_seen_ts"
    if newest:
        ordered = sorted(
            aliases.items(),
            key=lambda item: (_ts_epoch(item[1].get(key_name)), item[0]),
            reverse=True,
        )
    else:
        ordered = sorted(
            aliases.items(),
            key=lambda item: (_ts_epoch(item[1].get(key_name)), item[0]),
        )
    return ordered[0][0]


def _session_identity(project_id: Any, session_id: Any) -> tuple[str, str]:
    return (str(project_id or "default-project"), str(session_id or "unknown-session"))


def _extract_call_id(event: dict[str, Any], payload: dict[str, Any]) -> str | None:
    return _first_text(
        payload.get("call_id"),
        payload.get("callId"),
        event.get("call_id"),
        event.get("callId"),
    )


def _extract_message_id(event: dict[str, Any], payload: dict[str, Any]) -> str | None:
    return _first_text(payload.get("message_id"), payload.get("messageId"), event.get("message_id"))


def _extract_part_id(event: dict[str, Any], payload: dict[str, Any]) -> str | None:
    return _first_text(payload.get("part_id"), payload.get("partId"), event.get("part_id"))


def _extract_lineage_hints(event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    project_id = str(event.get("project_id") or "default-project")
    session_id = str(event.get("session_id") or "unknown-session")
    event_id = str(event.get("id") or "")
    ts = event.get("ts")
    evidence = _short_text(str(event.get("content") or ""), limit=300)

    def add_hint(source_path: str, target_path: str, relation: str | None = None) -> None:
        source = str(source_path or "").strip()
        target = str(target_path or "").strip()
        if not source or not target:
            return
        source_canonical = _normalize_path(source)
        target_canonical = _normalize_path(target)
        if not source_canonical or not target_canonical or source_canonical == target_canonical:
            return
        normalized_relation = _normalize_lineage_relation(relation)
        dedupe_key = (normalized_relation, source_canonical, target_canonical)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        hints.append(
            {
                "project_id": project_id,
                "session_id": session_id,
                "event_id": event_id,
                "ts": ts,
                "relation": normalized_relation,
                "source_path": source,
                "source_canonical_path": source_canonical,
                "target_path": target,
                "target_canonical_path": target_canonical,
                "evidence": evidence,
            }
        )

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            relation = _first_text(value.get("relation"), value.get("operation"), value.get("type"), default="rename")
            for source_key, target_key in (
                ("from", "to"),
                ("old_path", "new_path"),
                ("oldPath", "newPath"),
                ("old", "new"),
                ("source_path", "target_path"),
                ("sourcePath", "targetPath"),
                ("src", "dst"),
                ("src_path", "dst_path"),
                ("srcPath", "dstPath"),
                ("source", "target"),
            ):
                if source_key in value and target_key in value:
                    add_hint(str(value[source_key]), str(value[target_key]), relation)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)

    for command in _candidate_commands(event, payload):
        tokens = _safe_split_command(command)
        if not tokens:
            continue
        if tokens[0] == "git" and len(tokens) >= 4 and tokens[1] == "mv":
            add_hint(tokens[-2], tokens[-1], "git_mv")
        elif tokens[0] == "mv" and len(tokens) >= 3:
            add_hint(tokens[-2], tokens[-1], "move")
        elif tokens[0] == "cp" and len(tokens) >= 3:
            add_hint(tokens[-2], tokens[-1], "copy")
        elif tokens[0] == "rename" and len(tokens) >= 3:
            add_hint(tokens[-2], tokens[-1], "rename")
    return hints


def _candidate_commands(event: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            commands.append(command)
        elif isinstance(command, list):
            commands.append(" ".join(str(part) for part in command))
    elif isinstance(tool_input, str):
        commands.append(tool_input)
    if event.get("kind") == "tool_call" and event.get("content"):
        commands.append(str(event["content"]))
    return commands


def _safe_split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return [part for part in str(command).split() if part]


def _normalize_lineage_relation(value: str | None) -> str:
    relation = str(value or "rename").strip().lower()
    if relation in {"move", "mv", "git_mv"}:
        return "move" if relation != "git_mv" else "git_mv"
    if relation in {"copy", "cp"}:
        return "copy"
    return "rename"


def _extract_session_links(event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    project_id = str(event.get("project_id") or "default-project")
    session_id = str(event.get("session_id") or "unknown-session")
    event_id = str(event.get("id") or "")
    ts = event.get("ts")
    tool_name = event.get("tool_name")

    def add_link(parent_session_id: str | None, child_session_id: str | None, label: str = "delegates_to") -> None:
        parent = str(parent_session_id or "").strip()
        child = str(child_session_id or "").strip()
        if not parent or not child or parent == child:
            return
        dedupe_key = (parent, child, label)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        links.append(
            {
                "project_id": project_id,
                "parent_session_id": parent,
                "child_session_id": child,
                "label": label,
                "event_id": event_id,
                "ts": ts,
                "tool_name": tool_name,
            }
        )

    def visit(value: Any, *, context: str | None = None) -> None:
        if isinstance(value, dict):
            label = _first_text(value.get("label"), value.get("relation"), value.get("type"), context, default="delegates_to")
            parent = _first_text(
                value.get("parent_session_id"),
                value.get("parentSessionId"),
                value.get("parentSessionID"),
            )
            child_candidates = [
                _first_text(value.get("child_session_id"), value.get("childSessionId"), value.get("childSessionID")),
                _first_text(value.get("delegated_session_id"), value.get("delegatedSessionId"), value.get("delegatedSessionID")),
                _first_text(value.get("subagent_session_id"), value.get("subagentSessionId"), value.get("subagentSessionID")),
            ]
            for child in child_candidates:
                if child:
                    add_link(parent or session_id, child, label=label)
            if parent:
                add_link(parent, session_id, label=label)
            for key, nested in value.items():
                lowered = str(key).strip()
                if isinstance(nested, dict):
                    visit(nested, context=lowered)
                elif isinstance(nested, list):
                    visit(nested, context=lowered)
        elif isinstance(value, list):
            for item in value:
                visit(item, context=context)

    visit(payload)
    return links


def _chunk_text(text: str | None, *, max_tokens: int = 48, overlap: int = 12) -> list[str]:
    tokens = tokenize(text)
    if not tokens:
        return []
    if len(tokens) <= max_tokens:
        return [" ".join(tokens)]
    chunks: list[str] = []
    step = max(max_tokens - overlap, 1)
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start : start + max_tokens]
        if not chunk_tokens:
            continue
        chunks.append(" ".join(chunk_tokens))
        if start + max_tokens >= len(tokens):
            break
    return chunks


def _ts_epoch(value: Any) -> int:
    parsed = _parse_ts(str(value)) if value not in (None, "") else None
    if parsed is None:
        return 0
    return int(parsed.timestamp())


def _assert_read_only(sql: str) -> None:
    stripped = sql.strip().lower()
    if ";" in stripped.rstrip(";"):
        raise ValueError("Only a single read-only SQL statement is allowed")
    if stripped.startswith("pragma"):
        raise ValueError("PRAGMA statements are not allowed")
    if not stripped.startswith(("select", "with", "explain")):
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


def _base_event_id(event_id: str) -> str:
    if event_id.endswith(":call"):
        return event_id[: -len(":call")]
    if event_id.endswith(":result"):
        return event_id[: -len(":result")]
    return event_id


def _session_identity(project_id: Any, session_id: Any) -> tuple[str, str]:
    return (
        str(project_id or "default-project"),
        str(session_id or "unknown-session"),
    )


def _normalize_path(path: str | None) -> str:
    if not path:
        return ""
    value = str(path).replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    return value.lstrip("./")


def _short_text(text: str | None, *, limit: int = 200) -> str | None:
    if text is None:
        return None
    value = str(text).strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
