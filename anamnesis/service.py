from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from anamnesis.config import Settings
from anamnesis.models import CanonicalEvent
from anamnesis.query import MemoryQueryService
from anamnesis.storage import RawMemoryStore
from anamnesis.uqa_sidecar import UQASidecar


class MemoryService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def health(self) -> dict[str, Any]:
        raw_store = RawMemoryStore(self.settings.raw_db_path)
        raw_store.initialize()
        return {
            "workspace_root": str(self.settings.workspace_root),
            "raw_db_path": str(self.settings.raw_db_path),
            "uqa_sidecar": UQASidecar(
                self.settings.raw_db_path,
                self.settings.uqa_sidecar_path,
                repo_root=self.settings.uqa_repo_root,
            ).status(),
            "uqa_repo_root": str(self.settings.uqa_repo_root) if self.settings.uqa_repo_root else None,
            "mode": "uqa-mandatory",
        }

    def ingest(self, events: Iterable[CanonicalEvent], *, db_path: str | None = None) -> dict[str, Any]:
        raw_db_path = Path(db_path).resolve() if db_path else self.settings.raw_db_path
        store = RawMemoryStore(raw_db_path)
        count = store.append_events(events)
        sidecar = self.rebuild_uqa_sidecar(db_path=str(raw_db_path))
        return {
            "ingested": count,
            "db_path": str(store.db_path),
            "uqa_sidecar": sidecar,
        }

    def orient(self, *, db_path: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        return self._query(db_path).orient(project_id)

    def search(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
        backend: str = "uqa",
    ) -> dict[str, Any]:
        hits = self._query(db_path).search(query, limit=limit, project_id=project_id, backend=backend)
        return {
            "backend": "uqa",
            "results": hits,
            "hits": hits,
            "metadata": {"query": query, "limit": limit, "backend": "uqa"},
        }

    def trace_file(self, path: str, *, db_path: str | None = None, limit: int = 20) -> dict[str, Any]:
        result = self._query(db_path).trace_file(path, limit=limit)
        result["results"] = result.get("touches", [])
        return result

    def trace_decision(self, query: str, *, db_path: str | None = None, limit: int = 10) -> dict[str, Any]:
        result = self._query(db_path).trace_decision(query, limit=limit)
        result["results"] = result.get("sessions", [])
        return result

    def digest(self, *, days: int = 7, db_path: str | None = None) -> dict[str, Any]:
        return self._query(db_path).digest(days=days)

    def sql(
        self,
        sql: str,
        *,
        db_path: str | None = None,
        read_only: bool = True,
        backend: str = "uqa",
    ) -> dict[str, Any]:
        if not read_only:
            raise ValueError("Anamnesis exposes read-only SQL over the UQA sidecar")
        if backend.strip().lower() not in {"uqa", "anamnesis", "auto"}:
            raise ValueError("Anamnesis is UQA-only; backend must be 'uqa'")
        result = self._query(db_path).sql(sql)
        result["backend"] = "uqa"
        return result

    def rebuild_uqa_sidecar(self, *, db_path: str | None = None, sidecar_path: str | None = None) -> dict[str, Any]:
        raw_db = Path(db_path).resolve() if db_path else self.settings.raw_db_path
        sidecar = Path(sidecar_path).resolve() if sidecar_path else self._sidecar_path_for(raw_db)
        return UQASidecar(raw_db, sidecar, repo_root=self.settings.uqa_repo_root).rebuild()

    def _query(self, db_path: str | None = None) -> MemoryQueryService:
        raw_db = Path(db_path).resolve() if db_path else self.settings.raw_db_path
        store = RawMemoryStore(raw_db)
        store.initialize()
        return MemoryQueryService(
            store,
            sidecar_path=self._sidecar_path_for(raw_db),
            uqa_repo_root=self.settings.uqa_repo_root,
        )

    def _sidecar_path_for(self, raw_db: Path) -> Path:
        if raw_db == self.settings.raw_db_path:
            return self.settings.uqa_sidecar_path
        return raw_db.with_suffix(".uqa.db")
