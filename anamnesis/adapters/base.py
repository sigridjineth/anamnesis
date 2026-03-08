from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Iterable

from anamnesis.models import CanonicalEvent


class CaptureAdapter(ABC):
    agent: str

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        raise NotImplementedError


class BaseAdapter(CaptureAdapter):
    agent = "unknown"

    def _event_id(self, raw: dict[str, Any]) -> str:
        if raw.get("id"):
            return str(raw["id"])
        payload = json.dumps(raw, sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _session_id(self, raw: dict[str, Any]) -> str:
        session = raw.get("session")
        if isinstance(session, str) and session.strip():
            return session
        if isinstance(session, dict):
            for key in ("id", "session_id", "sessionId"):
                value = session.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return str(
            raw.get("session_id")
            or raw.get("sessionId")
            or raw.get("conversation_id")
            or "unknown-session"
        )

    def _project_id(self, raw: dict[str, Any]) -> str:
        session = raw.get("session")
        if isinstance(session, dict):
            for key in ("project_id", "projectId", "cwd", "working_directory", "workingDirectory"):
                value = session.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return str(
            raw.get("project_id")
            or raw.get("projectId")
            or raw.get("cwd")
            or raw.get("project")
            or "default-project"
        )

    def _ts(self, raw: dict[str, Any]) -> str:
        ts = raw.get("ts") or raw.get("timestamp") or raw.get("created_at")
        if ts is None:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(ts, (int, float)):
            value = float(ts)
            if abs(value) >= 1_000_000_000_000:
                value /= 1000
            return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")
        text = str(ts).strip()
        if not text:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            numeric = float(text)
        except ValueError:
            return text
        if abs(numeric) >= 1_000_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, UTC).isoformat().replace("+00:00", "Z")

    def _tool_name(self, raw: dict[str, Any]) -> str | None:
        for key in ("tool_name", "tool", "name"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
        tool_use = raw.get("tool_use")
        if isinstance(tool_use, dict):
            for key in ("name", "tool_name"):
                value = tool_use.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    def _target_path(self, raw: dict[str, Any]) -> str | None:
        for path in self._iter_paths(raw):
            return path
        return None

    def _file_touches_payload(
        self,
        raw: dict[str, Any],
        *,
        operation: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(raw)
        if payload.get("file_touches"):
            return payload
        paths = list(dict.fromkeys(self._iter_paths(raw)))
        if not paths:
            return payload
        inferred = operation or self._infer_operation(raw)
        payload["file_touches"] = [
            {"path": path, "operation": inferred}
            for path in paths
        ]
        return payload

    def _infer_operation(self, raw: dict[str, Any]) -> str:
        tool_name = (self._tool_name(raw) or "").lower()
        event_name = str(raw.get("event") or raw.get("type") or "").lower()
        if any(token in tool_name for token in ("write", "create")) or "create" in event_name:
            return "create"
        if any(token in tool_name for token in ("edit", "patch")) or "edit" in event_name:
            return "edit"
        if "delete" in tool_name or "delete" in event_name:
            return "delete"
        return "touch"

    def _iter_paths(self, raw: dict[str, Any]) -> Iterable[str]:
        seen: set[str] = set()
        path_keys = {
            "file",
            "path",
            "file_path",
            "filepath",
            "target_path",
            "target_file",
            "source_path",
            "notebook_path",
            "notebookpath",
        }

        def visit(value: Any) -> Iterable[str]:
            if isinstance(value, dict):
                for key, nested in value.items():
                    lowered = key.lower()
                    if lowered in path_keys and isinstance(nested, str) and nested.strip():
                        yield nested
                    elif lowered == "paths" and isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, str) and item.strip():
                                yield item
                            elif isinstance(item, dict):
                                yield from visit(item)
                    else:
                        yield from visit(nested)
            elif isinstance(value, list):
                for item in value:
                    yield from visit(item)

        for candidate in visit(raw):
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
