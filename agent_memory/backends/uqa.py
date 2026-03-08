from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_memory.config import WorkspaceConfig, ensure_repo_on_syspath
from agent_memory.contracts import QueryResponse, SchemaObject, SchemaSummary

_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|ANALYZE)\b", re.IGNORECASE | re.DOTALL)


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class UqaBackend:
    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        self.config = config or WorkspaceConfig.from_workspace()

    def _import_engine(self):
        ensure_repo_on_syspath(self.config.uqa_repo)
        try:
            from uqa.engine import Engine
        except Exception as exc:
            raise RuntimeError(
                "Unable to import UQA Engine. Ensure the cloned repo exists and its Python dependencies are installed."
            ) from exc
        return Engine

    def _resolve_db_path(self, db_path: str | None = None) -> Path | None:
        if db_path:
            path = Path(db_path).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"UQA database does not exist: {path}")
            return path
        return self.config.default_uqa_db_path

    def orient(self, db_path: str | None = None) -> SchemaSummary:
        resolved_path = self._resolve_db_path(db_path)
        Engine = self._import_engine()
        engine = Engine(db_path=str(resolved_path) if resolved_path else None)
        try:
            objects: list[SchemaObject] = []
            for table_name, table in sorted(engine._tables.items()):
                objects.append(
                    SchemaObject(
                        name=table_name,
                        kind="table",
                        columns=[
                            {
                                "name": col.name,
                                "type": col.type_name,
                                "primary_key": col.primary_key,
                                "not_null": col.not_null,
                            }
                            for col in table.columns.values()
                        ],
                    )
                )
            for view_name in sorted(engine._views):
                objects.append(SchemaObject(name=view_name, kind="view", columns=[]))
            return SchemaSummary(
                backend="uqa",
                target=str(resolved_path) if resolved_path else None,
                objects=objects,
                metadata={
                    "prepared_statement_count": len(engine._prepared),
                    "table_count": len(engine._tables),
                    "view_count": len(engine._views),
                },
            )
        finally:
            engine.close()

    def sql(self, query: str, db_path: str | None = None, read_only: bool = True) -> QueryResponse:
        if read_only and not _READ_ONLY_SQL.match(query.strip()):
            raise ValueError("UQA backend only allows read-only SELECT/WITH/EXPLAIN/ANALYZE queries")

        resolved_path = self._resolve_db_path(db_path)
        Engine = self._import_engine()
        engine = Engine(db_path=str(resolved_path) if resolved_path else None)
        try:
            result = engine.sql(query)
            rows = list(result.rows)
            return QueryResponse(
                backend="uqa",
                target=str(resolved_path) if resolved_path else None,
                columns=list(result.columns),
                rows=rows,
                metadata={"row_count": len(rows)},
            )
        finally:
            engine.close()

    def search(
        self,
        query: str,
        *,
        db_path: str | None = None,
        table: str | None = None,
        field: str | None = None,
        limit: int = 10,
    ) -> QueryResponse:
        resolved_path = self._resolve_db_path(db_path)
        Engine = self._import_engine()
        engine = Engine(db_path=str(resolved_path) if resolved_path else None)
        try:
            chosen_table = table
            chosen_field = field
            if chosen_table is None or chosen_field is None:
                for candidate_name, candidate_table in sorted(engine._tables.items()):
                    text_columns = [
                        col.name
                        for col in candidate_table.columns.values()
                        if getattr(col, "type_name", "").upper() == "TEXT"
                    ]
                    if text_columns:
                        chosen_table = chosen_table or candidate_name
                        chosen_field = chosen_field or text_columns[0]
                        break

            if chosen_table is None or chosen_field is None:
                raise ValueError("Could not infer a UQA text table/field. Pass table= and field= explicitly.")

            sql = (
                f"SELECT *, _score FROM {_quote_ident(chosen_table)} "
                f"WHERE text_match({_quote_ident(chosen_field)}, {_quote_string(query)}) "
                f"ORDER BY _score DESC LIMIT {int(limit)}"
            )
            result = engine.sql(sql)
            return QueryResponse(
                backend="uqa",
                target=str(resolved_path) if resolved_path else None,
                columns=list(result.columns),
                rows=list(result.rows),
                metadata={
                    "table": chosen_table,
                    "field": chosen_field,
                    "query": query,
                    "limit": limit,
                },
            )
        finally:
            engine.close()
