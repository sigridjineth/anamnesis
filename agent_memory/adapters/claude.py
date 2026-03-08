from __future__ import annotations

from typing import Any

from agent_memory.models import CanonicalEvent

from .base import BaseAdapter


class ClaudeAdapter(BaseAdapter):
    agent = "claude"

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        event_name = str(raw.get("event") or raw.get("hook_event_name") or raw.get("type") or "").lower()
        kind = "session_state"
        role = None
        content = raw.get("content") or raw.get("prompt") or raw.get("message")
        tool_name = self._tool_name(raw)
        if "prompt" in event_name:
            kind = "prompt"
            role = "user"
        elif "assistant" in event_name or raw.get("role") == "assistant":
            kind = "assistant_message"
            role = "assistant"
        elif "tool" in event_name:
            kind = "tool_result" if raw.get("result") or raw.get("output") else "tool_call"
            role = "tool"
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
                target_path=self._target_path(raw),
                payload=self._file_touches_payload(raw),
            )
        ]
