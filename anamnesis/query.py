from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage import RawMemoryStore
from .uqa_sidecar import UQASidecar


class MemoryQueryService:
    def __init__(
        self,
        store: RawMemoryStore,
        *,
        sidecar_path: str | Path | None = None,
        uqa_repo_root: Path | None = None,
    ):
        self.store = store
        self.sidecar = UQASidecar(
            store.db_path,
            sidecar_path=sidecar_path,
            repo_root=uqa_repo_root,
        )

    def orient(self, project_id: str | None = None) -> dict[str, Any]:
        return self.sidecar.orient(project_id=project_id)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
        entity_types: list[str] | None = None,
        backend: str = "uqa",
    ) -> list[dict[str, Any]]:
        _assert_uqa_backend(backend)
        return self.sidecar.search(query, limit=limit, project_id=project_id, entity_types=entity_types)

    def file_search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.sidecar.file_search(query, limit=limit, project_id=project_id)

    def trace_file(
        self,
        path: str,
        *,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.trace_file(path, limit=limit, project_id=project_id)

    def trace_decision(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.trace_decision(query, limit=limit, project_id=project_id)

    def story(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.story(session_id=session_id, query=query, limit=limit, project_id=project_id)

    def sprints(self, *, days: int = 14, project_id: str | None = None, gap_hours: int = 4) -> dict[str, Any]:
        return self.sidecar.sprints(days=days, project_id=project_id, gap_hours=gap_hours)

    def genealogy(
        self,
        query: str,
        *,
        limit: int = 20,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.genealogy(query, limit=limit, project_id=project_id)

    def bridges(
        self,
        query_a: str,
        query_b: str | None = None,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.bridges(query_a, query_b, limit=limit, project_id=project_id)

    def delegation_tree(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sidecar.delegation_tree(session_id=session_id, query=query, limit=limit, project_id=project_id)

    def digest(self, *, days: int = 7, project_id: str | None = None) -> dict[str, Any]:
        return self.sidecar.digest(days=days, project_id=project_id)

    def sql(self, sql: str) -> dict[str, Any]:
        return self.sidecar.sql(sql)


def _assert_uqa_backend(backend: str) -> None:
    normalized = backend.strip().lower()
    if normalized not in {"uqa", "anamnesis", "auto"}:
        raise ValueError("Anamnesis is UQA-only; backend must be 'uqa'")
