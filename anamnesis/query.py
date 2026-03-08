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
        backend: str = "uqa",
    ) -> list[dict[str, Any]]:
        _assert_uqa_backend(backend)
        return self.sidecar.search(query, limit=limit, project_id=project_id)

    def trace_file(self, path: str, *, limit: int = 20) -> dict[str, Any]:
        return self.sidecar.trace_file(path, limit=limit)

    def trace_decision(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        return self.sidecar.trace_decision(query, limit=limit)

    def digest(self, *, days: int = 7) -> dict[str, Any]:
        return self.sidecar.digest(days=days)

    def sql(self, sql: str) -> dict[str, Any]:
        return self.sidecar.sql(sql)


def _assert_uqa_backend(backend: str) -> None:
    normalized = backend.strip().lower()
    if normalized not in {"uqa", "anamnesis", "auto"}:
        raise ValueError("Anamnesis is UQA-only; backend must be 'uqa'")
