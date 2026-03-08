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

    def health(self, *, db_path: str | None = None) -> dict[str, Any]:
        raw_db = Path(db_path).resolve() if db_path else self.settings.raw_db_path
        sidecar_path = self._sidecar_path_for(raw_db)
        raw_store = RawMemoryStore(raw_db)
        raw_store.initialize()
        sidecar = UQASidecar(
            raw_db,
            sidecar_path,
            repo_root=self.settings.uqa_repo_root,
        )
        return {
            "workspace_root": str(self.settings.workspace_root),
            "raw_db_path": str(raw_db),
            "uqa_sidecar_path": str(sidecar_path),
            "uqa_repo_root": str(self.settings.uqa_repo_root) if self.settings.uqa_repo_root else None,
            "mode": "uqa-mandatory",
            "uqa": sidecar.health(),
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

    def survey(self, *, db_path: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        return self.orient(db_path=db_path, project_id=project_id)

    def search(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
        entity_types: list[str] | None = None,
        backend: str = "uqa",
    ) -> dict[str, Any]:
        hits = self._query(db_path).search(
            query,
            limit=limit,
            project_id=project_id,
            entity_types=entity_types,
            backend=backend,
        )
        return {
            "backend": "uqa",
            "results": hits,
            "hits": hits,
            "metadata": {
                "query": query,
                "limit": limit,
                "backend": "uqa",
                "entity_types": entity_types,
            },
        }

    def file_search(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        files = self._query(db_path).file_search(query, limit=limit, project_id=project_id)
        return {
            "backend": "uqa",
            "query": query,
            "files": files,
            "results": files,
            "metadata": {"limit": limit, "project_id": project_id},
        }

    def trace_file(
        self,
        path: str,
        *,
        db_path: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).trace_file(path, limit=limit, project_id=project_id)
        result["results"] = result.get("touches", [])
        return result

    def artifact(
        self,
        path: str,
        *,
        db_path: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.trace_file(path, db_path=db_path, limit=limit, project_id=project_id)

    def trace_decision(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).trace_decision(query, limit=limit, project_id=project_id)
        result["results"] = result.get("sessions", [])
        return result

    def thesis(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.trace_decision(query, db_path=db_path, limit=limit, project_id=project_id)

    def story(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        db_path: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).story(session_id=session_id, query=query, limit=limit, project_id=project_id)
        result["results"] = result.get("timeline", [])
        return result

    def chronicle(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        db_path: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.story(
            session_id=session_id,
            query=query,
            db_path=db_path,
            limit=limit,
            project_id=project_id,
        )

    def sprints(
        self,
        *,
        days: int = 14,
        db_path: str | None = None,
        project_id: str | None = None,
        gap_hours: int = 4,
    ) -> dict[str, Any]:
        return self._query(db_path).sprints(days=days, project_id=project_id, gap_hours=gap_hours)

    def cadence(
        self,
        *,
        days: int = 14,
        db_path: str | None = None,
        project_id: str | None = None,
        gap_hours: int = 4,
    ) -> dict[str, Any]:
        return self.sprints(days=days, db_path=db_path, project_id=project_id, gap_hours=gap_hours)

    def genealogy(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).genealogy(query, limit=limit, project_id=project_id)
        result["results"] = result.get("timeline", [])
        return result

    def lineage(
        self,
        query: str,
        *,
        db_path: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.genealogy(query, db_path=db_path, limit=limit, project_id=project_id)

    def bridges(
        self,
        query_a: str,
        query_b: str | None = None,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).bridges(query_a, query_b, limit=limit, project_id=project_id)
        if query_b:
            result["results"] = result.get("shared_files", [])
        else:
            result["results"] = result.get("bridges", [])
        return result

    def crossroads(
        self,
        query_a: str,
        query_b: str | None = None,
        *,
        db_path: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.bridges(
            query_a,
            query_b,
            db_path=db_path,
            limit=limit,
            project_id=project_id,
        )

    def delegation_tree(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        db_path: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        result = self._query(db_path).delegation_tree(
            session_id=session_id,
            query=query,
            limit=limit,
            project_id=project_id,
        )
        result["results"] = [
            step
            for session in result.get("sessions", [])
            for step in session.get("steps", [])
        ]
        return result

    def relay(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        db_path: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.delegation_tree(
            session_id=session_id,
            query=query,
            db_path=db_path,
            limit=limit,
            project_id=project_id,
        )

    def digest(self, *, days: int = 7, db_path: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        return self._query(db_path).digest(days=days, project_id=project_id)

    def synopsis(self, *, days: int = 7, db_path: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        return self.digest(days=days, db_path=db_path, project_id=project_id)

    def vitals(self, *, db_path: str | None = None) -> dict[str, Any]:
        return self.health(db_path=db_path)

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
