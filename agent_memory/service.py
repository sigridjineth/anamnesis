from __future__ import annotations

from pathlib import Path
from typing import Iterable, Any

from agent_memory.config import Settings
from agent_memory.flex_bridge import discover_flex_cells
from agent_memory.generic_sqlite import GenericSQLiteExplorer
from agent_memory.models import CanonicalEvent
from agent_memory.query import MemoryQueryService
from agent_memory.storage import RawMemoryStore
from agent_memory.uqa_sidecar import UQASidecar


class MemoryService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def health(self) -> dict[str, Any]:
        raw_store = RawMemoryStore(self.settings.raw_db_path)
        raw_store.initialize()
        return {
            "workspace_root": str(self.settings.workspace_root),
            "raw_db_path": str(self.settings.raw_db_path),
            "uqa_sidecar": UQASidecar(self.settings.raw_db_path, self.settings.uqa_sidecar_path).status(),
            "flex_repo_root": str(self.settings.flex_repo_root) if self.settings.flex_repo_root else None,
            "uqa_repo_root": str(self.settings.uqa_repo_root) if self.settings.uqa_repo_root else None,
            "flex_cells": discover_flex_cells(),
        }

    def ingest(self, events: Iterable[CanonicalEvent], *, db_path: str | None = None) -> dict[str, Any]:
        store = RawMemoryStore(db_path or self.settings.raw_db_path)
        count = store.append_events(events)
        return {"ingested": count, "db_path": str(store.db_path)}

    def orient(self, *, db_path: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        default_path = db_path or self._default_db_path()
        if default_path and not self._use_canonical_store(db_path, default_path):
            return self._generic(default_path).orient()
        return self._query(default_path).orient(project_id)

    def search(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
        backend: str = "auto",
    ) -> dict[str, Any]:
        target = db_path or self._default_db_path()
        if target and not self._use_canonical_store(db_path, target):
            result = self._generic(target).search(query, limit=limit)
            return {
                "backend": "flex-like",
                "results": result["hits"],
                "hits": result["hits"],
                "metadata": result["metadata"],
            }
        hits = self._query(target).search(
            query,
            limit=limit,
            project_id=project_id,
            backend=backend,
        )
        return {
            "backend": "canonical",
            "results": hits,
            "hits": hits,
            "metadata": {"query": query, "limit": limit, "backend": backend},
        }

    def trace_file(self, path: str, *, db_path: str | None = None, limit: int = 20) -> dict[str, Any]:
        target = db_path or self._default_db_path()
        if target and not self._use_canonical_store(db_path, target):
            result = self._generic(target).trace_file(path, limit=limit)
            result["results"] = result["hits"]
            return result
        result = self._query(target).trace_file(path, limit=limit)
        result["results"] = result.get("touches", [])
        return result

    def trace_decision(self, query: str, *, db_path: str | None = None, limit: int = 10) -> dict[str, Any]:
        target = db_path or self._default_db_path()
        if target and not self._use_canonical_store(db_path, target):
            result = self._generic(target).search(query, limit=limit)
            result["results"] = result["hits"]
            return result
        result = self._query(target).trace_decision(query, limit=limit)
        result["results"] = result.get("sessions", [])
        return result

    def digest(self, *, days: int = 7, db_path: str | None = None) -> dict[str, Any]:
        target = db_path or self._default_db_path()
        if target and not self._use_canonical_store(db_path, target):
            return self._generic(target).digest(days=days)
        return self._query(target).digest(days=days)

    def sql(
        self,
        sql: str,
        *,
        db_path: str | None = None,
        read_only: bool = True,
        backend: str = "auto",
    ) -> dict[str, Any]:
        target = db_path or self._default_db_path()
        if target and not self._use_canonical_store(db_path, target):
            result = self._generic(target).sql(sql, read_only=read_only)
            result["backend"] = "flex"
            return result
        if not read_only:
            raise ValueError("Mutation through MemoryService.sql is not supported for canonical stores")
        result = self._query(target).sql(sql)
        result["backend"] = "canonical"
        return result

    def rebuild_uqa_sidecar(self, *, db_path: str | None = None, sidecar_path: str | None = None) -> dict[str, Any]:
        raw_db = Path(db_path).resolve() if db_path else self.settings.raw_db_path
        sidecar = Path(sidecar_path).resolve() if sidecar_path else self.settings.uqa_sidecar_path
        return UQASidecar(raw_db, sidecar).rebuild()

    def _query(self, db_path: str | None = None) -> MemoryQueryService:
        store = RawMemoryStore(db_path or self.settings.raw_db_path)
        store.initialize()
        return MemoryQueryService(store)

    def _generic(self, db_path: str) -> GenericSQLiteExplorer:
        return GenericSQLiteExplorer(db_path)

    def _is_canonical_db(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            explorer = GenericSQLiteExplorer(path)
            names = {obj["name"] for obj in explorer.orient()["objects"]}
            return {"sessions", "events", "file_touches"}.issubset(names)
        except Exception:
            return False

    def _use_canonical_store(self, explicit_db_path: str | None, target: str) -> bool:
        path = Path(target)
        if explicit_db_path is None and self.settings.raw_db_path is not None:
            raw_path = self.settings.raw_db_path.resolve()
            if path.resolve() == raw_path and (
                raw_path.exists() or self.settings.flex_cell_path is None
            ):
                return True
        return self._is_canonical_db(path)

    def _default_db_path(self) -> str | None:
        if self.settings.raw_db_path is not None and self.settings.raw_db_path.exists():
            return str(self.settings.raw_db_path)
        if self.settings.flex_cell_path is not None:
            return str(self.settings.flex_cell_path)
        if self.settings.raw_db_path is not None:
            return str(self.settings.raw_db_path)
        return None
