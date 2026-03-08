from __future__ import annotations

from typing import Any

from anamnesis.models import CanonicalEvent

from .base import BaseAdapter


class ClaudeAdapter(BaseAdapter):
    agent = "claude"

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        event_name = str(raw.get("event") or raw.get("hook_event_name") or raw.get("type") or "").lower()
        kind = "session_state"
        role = None
        content = raw.get("content") or raw.get("prompt") or raw.get("message")
        tool_name = self._tool_name(raw)
        if event_name in {"user", "human"} or "prompt" in event_name:
            kind = "prompt"
            role = "user"
        elif event_name in {"assistant", "model"} or "assistant" in event_name or raw.get("role") == "assistant":
            kind = "assistant_message"
            role = "assistant"
        elif event_name in {"tool_use", "tool_result"} or "tool" in event_name:
            kind = "tool_result" if raw.get("result") or raw.get("output") else "tool_call"
            if event_name == "tool_result" or raw.get("tool_output"):
                kind = "tool_result"
            role = "tool"
            content = self._tool_content(raw, kind)
        elif "file" in event_name:
            kind = "file_touch"
        return [
            CanonicalEvent(
                id=self._event_id(raw),
                agent="claude",
                session_id=self._session_id(raw),
                project_id=self._project_id(raw),
                ts=self._ts(raw),
                kind=kind,
                role=role,
                content=str(content) if content is not None else None,
                tool_name=str(tool_name) if tool_name is not None else None,
                target_path=self._target_path(raw) if kind != "tool_result" else None,
                payload=self._file_touches_payload(raw) if kind != "tool_result" else dict(raw),
            )
        ]

    def _tool_content(self, raw: dict[str, Any], kind: str) -> str | None:
        if kind == "tool_call":
            tool_input = raw.get("tool_input")
            if isinstance(tool_input, dict):
                for key in ("command", "path", "filePath", "file_path", "query"):
                    value = tool_input.get(key)
                    if isinstance(value, list):
                        return " ".join(str(part) for part in value)
                    if isinstance(value, str) and value.strip():
                        return value
            if isinstance(tool_input, str) and tool_input.strip():
                return tool_input
            return None
        tool_output = raw.get("tool_output") or raw.get("result") or raw.get("output")
        if isinstance(tool_output, dict):
            for key in ("preview", "output", "result", "message", "text"):
                value = tool_output.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        if isinstance(tool_output, str) and tool_output.strip():
            return tool_output
        return None
