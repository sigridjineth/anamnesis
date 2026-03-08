from __future__ import annotations

import json
from typing import Any

from anamnesis.models import CanonicalEvent

from .base import BaseAdapter


class CodexAdapter(BaseAdapter):
    agent = "codex"

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        if self._record_type(raw) == "reasoning":
            return []
        if self._is_post_tool_hook(raw):
            return self._normalize_post_tool_hook(raw)
        event = self._normalize_single(raw)
        return [event] if event is not None else []

    def _normalize_single(self, raw: dict[str, Any]) -> CanonicalEvent | None:
        kind, role = self._kind_and_role(raw)
        content = self._content(raw, kind)
        tool_name = self._tool_name(raw)
        target_path = self._target_path(raw) if kind != "tool_result" else None
        return CanonicalEvent(
            id=self._event_id(raw),
            agent="codex",
            session_id=self._session_id(raw),
            project_id=self._project_id(raw),
            ts=self._ts(raw),
            kind=kind,
            role=role,
            content=content,
            tool_name=str(tool_name) if tool_name is not None else None,
            target_path=target_path,
            payload=self._compact_payload(raw, include_file_touches=kind != "tool_result"),
        )

    def _normalize_post_tool_hook(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        base_id = self._event_id(raw)
        session_id = self._session_id(raw)
        project_id = self._project_id(raw)
        ts = self._ts(raw)
        tool_name = self._tool_name(raw)
        target_path = self._target_path(raw)
        call_payload = self._compact_payload(raw, include_file_touches=True)
        result_payload = self._compact_payload(raw, include_file_touches=False)
        call_content = self._content(raw, "tool_call")
        result_content = self._content(raw, "tool_result")
        return [
            CanonicalEvent(
                id=f"{base_id}:call",
                agent="codex",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="tool_call",
                role="tool",
                content=call_content,
                tool_name=str(tool_name) if tool_name is not None else None,
                target_path=target_path,
                payload=call_payload,
            ),
            CanonicalEvent(
                id=f"{base_id}:result",
                agent="codex",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="tool_result",
                role="tool",
                content=result_content,
                tool_name=str(tool_name) if tool_name is not None else None,
                target_path=None,
                payload=result_payload,
            ),
        ]

    def _kind_and_role(self, raw: dict[str, Any]) -> tuple[str, str | None]:
        record_type = self._record_type(raw)
        role = str(raw.get("role") or "assistant")
        if self._is_prompt(raw):
            return "prompt", "user"
        if record_type == "message":
            if role == "user":
                return "prompt", "user"
            return "assistant_message", "assistant"
        if record_type == "function_call":
            return "tool_call", "tool"
        if record_type == "function_call_output":
            return "tool_result", "tool"
        if "tool" in record_type:
            return ("tool_result" if self._has_tool_output(raw) else "tool_call"), "tool"
        if "permission" in record_type:
            return "permission", role
        if "file" in record_type:
            return "file_touch", None
        if "session" in record_type or record_type == "stop":
            return "session_state", None
        return "assistant_message", role

    def _record_type(self, raw: dict[str, Any]) -> str:
        record_type = str(raw.get("type") or raw.get("event") or "").strip().lower()
        if record_type:
            return record_type
        tool_name = str(raw.get("tool") or "").strip().lower()
        if tool_name == "userprompt":
            return "user_prompt"
        if tool_name:
            return "tool_hook"
        if raw.get("text") is not None:
            return "history"
        return ""

    def _is_prompt(self, raw: dict[str, Any]) -> bool:
        record_type = self._record_type(raw)
        tool_name = str(raw.get("tool") or "").strip().lower()
        if tool_name == "userprompt":
            return True
        if record_type == "history":
            return True
        if record_type == "user_prompt":
            return True
        if record_type == "message" and str(raw.get("role") or "").lower() == "user":
            return True
        return False

    def _is_post_tool_hook(self, raw: dict[str, Any]) -> bool:
        return (
            bool(raw.get("tool"))
            and str(raw.get("tool")).strip().lower() != "userprompt"
            and not raw.get("type")
            and not raw.get("event")
        )

    def _has_tool_output(self, raw: dict[str, Any]) -> bool:
        return any(raw.get(key) not in (None, "", {}) for key in ("response", "output", "result"))

    def _content(self, raw: dict[str, Any], kind: str) -> str | None:
        if kind in {"prompt", "assistant_message"}:
            return self._message_text(
                raw.get("content")
                or raw.get("message")
                or raw.get("prompt")
                or raw.get("text")
            )
        if kind == "tool_call":
            tool_input = self._tool_input(raw)
            for key in ("prompt", "command", "query", "url", "file"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            if isinstance(tool_input, dict):
                command = tool_input.get("command")
                if isinstance(command, list):
                    return " ".join(str(part) for part in command)
                if isinstance(command, str) and command.strip():
                    return command
                for key in ("prompt", "query", "url", "file", "file_path", "path"):
                    value = tool_input.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
                return self._trim(json.dumps(tool_input, sort_keys=True, default=str))
            if isinstance(tool_input, str) and tool_input.strip():
                return self._trim(tool_input)
            return None
        if kind == "tool_result":
            output = self._tool_output(raw)
            if isinstance(output, dict):
                for key in ("output", "result", "text", "message"):
                    value = output.get(key)
                    if isinstance(value, str) and value.strip():
                        return self._trim(value)
                return self._trim(json.dumps(output, sort_keys=True, default=str))
            if isinstance(output, str) and output.strip():
                return self._trim(output)
            return None
        return None

    def _message_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text)
            return "\n".join(parts) if parts else None
        if isinstance(value, dict):
            text = value.get("text") or value.get("content")
            if isinstance(text, str) and text.strip():
                return text
            return self._trim(json.dumps(value, sort_keys=True, default=str))
        return str(value)

    def _tool_input(self, raw: dict[str, Any]) -> Any:
        for key in ("tool_input", "toolInput", "arguments", "input"):
            if key not in raw:
                continue
            return self._maybe_json(raw.get(key))
        return None

    def _tool_output(self, raw: dict[str, Any]) -> Any:
        for key in ("response", "output", "result", "tool_response", "toolResponse"):
            if key not in raw:
                continue
            return self._maybe_json(raw.get(key))
        return None

    def _maybe_json(self, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return value

    def _compact_payload(
        self,
        raw: dict[str, Any],
        *,
        include_file_touches: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for source_key, target_key in (
            ("_source", "source"),
            ("type", "raw_type"),
            ("event", "raw_event"),
            ("status", "status"),
            ("call_id", "call_id"),
            ("_item_index", "item_index"),
            ("msg", "message_index"),
            ("_session_timestamp", "session_timestamp"),
        ):
            value = raw.get(source_key)
            if value not in (None, "", []):
                payload[target_key] = value
        tool_input = self._tool_input(raw)
        if tool_input not in (None, "", {}):
            payload["tool_input"] = tool_input
        output = self._tool_output(raw)
        if output not in (None, "", {}):
            payload["tool_output_preview"] = self._trim(
                output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str),
                limit=4000,
            )
        paths = list(dict.fromkeys(self._iter_paths(raw)))
        if include_file_touches and paths:
            payload["file_touches"] = [
                {"path": path, "operation": self._infer_operation(raw)}
                for path in paths
            ]
        return payload

    def _trim(self, value: str, *, limit: int = 16000) -> str:
        text = str(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…"
