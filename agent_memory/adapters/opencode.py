from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Iterable

from agent_memory.models import CanonicalEvent

from .base import BaseAdapter


class OpenCodeAdapter(BaseAdapter):
    agent = "opencode"

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        if self._is_export_document(raw):
            return self._normalize_export_document(raw)
        record_type = self._record_type(raw)
        if record_type == "chat.message":
            return [self._normalize_chat_message(raw)]
        if record_type in {"tool.execute.before", "tool.execute.after"}:
            return self._normalize_tool_hook(raw)
        if record_type == "message.part.updated":
            event = self._normalize_part_update(raw)
            return [event] if event is not None else []
        if record_type == "file.edited":
            return [self._normalize_file_event(raw)]
        if record_type.startswith("session."):
            return [self._normalize_session_event(raw)]
        return [self._normalize_fallback(raw)]

    def _normalize_export_document(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        info = raw.get("info")
        if not isinstance(info, dict):
            return []
        session_id = str(info.get("id") or "unknown-session")
        project_id = self._project_id(raw)
        session_ts = self._ts_from_ms(info.get("time", {}).get("created"))
        events: list[CanonicalEvent] = [
            CanonicalEvent(
                id=f"{session_id}:session",
                agent="opencode",
                session_id=session_id,
                project_id=project_id,
                ts=session_ts,
                kind="session_state",
                role=None,
                content=str(info.get("title") or ""),
                payload={
                    "source": "opencode_export",
                    "directory": info.get("directory"),
                    "project_id": info.get("projectID"),
                    "slug": info.get("slug"),
                    "version": info.get("version"),
                },
            )
        ]

        messages = raw.get("messages")
        if not isinstance(messages, list):
            return events
        for message in messages:
            if not isinstance(message, dict):
                continue
            events.extend(self._normalize_export_message(message, project_id=project_id))
        return events

    def _normalize_export_message(
        self,
        message: dict[str, Any],
        *,
        project_id: str,
    ) -> list[CanonicalEvent]:
        info = message.get("info")
        if not isinstance(info, dict):
            return []
        parts = message.get("parts")
        if not isinstance(parts, list):
            parts = []
        session_id = str(info.get("sessionID") or self._session_id(message))
        role = str(info.get("role") or "assistant")
        ts = self._ts_from_ms(info.get("time", {}).get("created"))
        events: list[CanonicalEvent] = []

        content = self._export_message_content(info, parts)
        if content:
            events.append(
                CanonicalEvent(
                    id=str(info.get("id") or self._event_id(message)),
                    agent="opencode",
                    session_id=session_id,
                    project_id=project_id,
                    ts=ts,
                    kind="prompt" if role == "user" else "assistant_message",
                    role="user" if role == "user" else "assistant",
                    content=content,
                    payload=self._export_message_payload(info, parts),
                )
            )

        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type == "tool":
                events.extend(self._events_from_tool_part(part, project_id=project_id, fallback_ts=ts))
            elif part_type == "patch":
                files = [str(path) for path in part.get("files") or [] if str(path).strip()]
                if files:
                    events.append(
                        CanonicalEvent(
                            id=str(part.get("id") or self._event_id(part)),
                            agent="opencode",
                            session_id=session_id,
                            project_id=project_id,
                            ts=ts,
                            kind="file_touch",
                            role=None,
                            content=str(part.get("hash") or ""),
                            target_path=files[0],
                            payload={
                                "source": "opencode_export",
                                "part_type": "patch",
                                "file_touches": [
                                    {"path": path, "operation": "edit"}
                                    for path in files
                                ],
                            },
                        )
                    )
        return events

    def _normalize_chat_message(self, raw: dict[str, Any]) -> CanonicalEvent:
        message = raw.get("message")
        parts = raw.get("parts") if isinstance(raw.get("parts"), list) else []
        content = self._extract_text_parts(parts)
        if content is None and isinstance(message, dict):
            content = self._extract_text_parts(message.get("parts") or [])
        if content is None and isinstance(message, dict):
            content = self._message_text(message.get("text") or message.get("content"))
        session_id = self._session_id(raw)
        project_id = self._project_id(raw)
        return CanonicalEvent(
            id=self._event_id(raw),
            agent="opencode",
            session_id=session_id,
            project_id=project_id,
            ts=self._ts(raw),
            kind="prompt",
            role="user",
            content=content,
            payload={
                "source": raw.get("_source") or "opencode_live",
                "parts": parts,
            },
        )

    def _normalize_tool_hook(self, raw: dict[str, Any]) -> list[CanonicalEvent]:
        record_type = self._record_type(raw)
        session_id = self._session_id(raw)
        project_id = self._project_id(raw)
        ts = self._ts(raw)
        tool_name = self._tool_name(raw)
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        tool_input = raw.get("args") if isinstance(raw.get("args"), dict) else metadata.get("input")
        output_text = raw.get("output")
        payload = self._hook_payload(raw)
        target_path = self._target_path({"tool_input": tool_input, **raw})

        if record_type == "tool.execute.before":
            return [
                CanonicalEvent(
                    id=f"{self._event_id(raw)}:call",
                    agent="opencode",
                    session_id=session_id,
                    project_id=project_id,
                    ts=ts,
                    kind="tool_call",
                    role="tool",
                    content=self._tool_input_content(tool_input),
                    tool_name=str(tool_name) if tool_name is not None else None,
                    target_path=target_path,
                    payload=payload,
                )
            ]

        return [
            CanonicalEvent(
                id=f"{self._event_id(raw)}:result",
                agent="opencode",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="tool_result",
                role="tool",
                content=self._trim(str(output_text or raw.get("title") or "")) or None,
                tool_name=str(tool_name) if tool_name is not None else None,
                target_path=None,
                payload=payload,
            )
        ]

    def _normalize_part_update(self, raw: dict[str, Any]) -> CanonicalEvent | None:
        props = self._properties(raw)
        part = props.get("part")
        if not isinstance(part, dict):
            return None
        part_type = str(part.get("type") or "").lower()
        session_id = str(part.get("sessionID") or self._session_id(raw))
        project_id = self._project_id(raw)
        ts = self._ts_from_ms((part.get("time") or {}).get("start")) if isinstance(part.get("time"), dict) else self._ts(raw)

        if part_type == "text":
            content = props.get("delta") or part.get("text")
            return CanonicalEvent(
                id=str(part.get("id") or self._event_id(raw)),
                agent="opencode",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="assistant_message",
                role="assistant",
                content=str(content) if content is not None else None,
                payload={"source": raw.get("_source") or "opencode_live", "part_type": part_type},
            )

        if part_type == "patch":
            files = [str(path) for path in part.get("files") or [] if str(path).strip()]
            if not files:
                return None
            return CanonicalEvent(
                id=str(part.get("id") or self._event_id(raw)),
                agent="opencode",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="file_touch",
                role=None,
                content=str(part.get("hash") or ""),
                target_path=files[0],
                payload={
                    "source": raw.get("_source") or "opencode_live",
                    "part_type": part_type,
                    "file_touches": [{"path": path, "operation": "edit"} for path in files],
                },
            )
        return None

    def _normalize_file_event(self, raw: dict[str, Any]) -> CanonicalEvent:
        props = self._properties(raw)
        path = props.get("file") or raw.get("file") or raw.get("path")
        return CanonicalEvent(
            id=self._event_id(raw),
            agent="opencode",
            session_id=self._session_id(raw),
            project_id=self._project_id(raw),
            ts=self._ts(raw),
            kind="file_touch",
            role=None,
            content=None,
            target_path=str(path) if path is not None else None,
            payload={
                "source": raw.get("_source") or "opencode_live",
                "file_touches": (
                    [{"path": str(path), "operation": "edit"}]
                    if path is not None
                    else []
                ),
            },
        )

    def _normalize_session_event(self, raw: dict[str, Any]) -> CanonicalEvent:
        props = self._properties(raw)
        session_id = props.get("sessionID") or raw.get("sessionID") or self._session_id(raw)
        return CanonicalEvent(
            id=self._event_id(raw),
            agent="opencode",
            session_id=str(session_id),
            project_id=self._project_id(raw),
            ts=self._ts(raw),
            kind="session_state",
            role=None,
            content=str(raw.get("type") or ""),
            payload={"source": raw.get("_source") or "opencode_live", **props},
        )

    def _normalize_fallback(self, raw: dict[str, Any]) -> CanonicalEvent:
        event_name = self._record_type(raw)
        role = str(raw.get("role") or "assistant")
        kind = "assistant_message"
        if event_name.startswith("message.user") or role == "user":
            kind = "prompt"
            role = "user"
        elif event_name.startswith("tool.execute.before"):
            kind = "tool_call"
            role = "tool"
        elif event_name.startswith("tool.execute.after"):
            kind = "tool_result"
            role = "tool"
        elif event_name.startswith("file."):
            kind = "file_touch"
            role = None
        elif event_name.startswith("session."):
            kind = "session_state"
            role = None

        return CanonicalEvent(
            id=self._event_id(raw),
            agent="opencode",
            session_id=self._session_id(raw),
            project_id=self._project_id(raw),
            ts=self._ts(raw),
            kind=kind,
            role=role,
            content=self._message_text(raw.get("content") or raw.get("message")),
            tool_name=self._tool_name(raw),
            target_path=self._target_path(raw),
            payload=self._file_touches_payload(raw),
        )

    def _events_from_tool_part(
        self,
        part: dict[str, Any],
        *,
        project_id: str,
        fallback_ts: str,
    ) -> list[CanonicalEvent]:
        state = part.get("state")
        if not isinstance(state, dict):
            return []
        session_id = str(part.get("sessionID") or "unknown-session")
        status = str(state.get("status") or "")
        ts = self._ts_from_ms((state.get("time") or {}).get("start")) if isinstance(state.get("time"), dict) else fallback_ts
        tool_name = str(part.get("tool") or "")
        call_id = str(part.get("callID") or part.get("id") or self._event_id(part))
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else None
        payload_base = {
            "source": "opencode_export",
            "part_type": "tool",
            "status": status,
            "call_id": call_id,
            "metadata": state.get("metadata"),
        }
        file_touches = self._file_touches_from_tool(tool_input, state)
        if file_touches:
            payload_base["file_touches"] = file_touches
        target_path = file_touches[0]["path"] if file_touches else None
        events: list[CanonicalEvent] = [
            CanonicalEvent(
                id=f"{part.get('id') or call_id}:call",
                agent="opencode",
                session_id=session_id,
                project_id=project_id,
                ts=ts,
                kind="tool_call",
                role="tool",
                content=self._tool_input_content(tool_input),
                tool_name=tool_name or None,
                target_path=target_path,
                payload={**payload_base, "tool_input": tool_input},
            )
        ]

        if status in {"completed", "error"}:
            result_content = state.get("output") if status == "completed" else state.get("error")
            attachments = state.get("attachments") if isinstance(state.get("attachments"), list) else []
            result_payload = {k: v for k, v in payload_base.items() if k != "file_touches"}
            if attachments:
                result_payload["attachments"] = attachments
            events.append(
                CanonicalEvent(
                    id=f"{part.get('id') or call_id}:result",
                    agent="opencode",
                    session_id=session_id,
                    project_id=project_id,
                    ts=self._ts_from_ms((state.get("time") or {}).get("end")) if isinstance(state.get("time"), dict) and (state.get("time") or {}).get("end") else ts,
                    kind="tool_result",
                    role="tool",
                    content=self._trim(str(result_content or state.get("title") or "")) or None,
                    tool_name=tool_name or None,
                    target_path=None,
                    payload=result_payload,
                )
            )
        return events

    def _is_export_document(self, raw: dict[str, Any]) -> bool:
        return isinstance(raw.get("info"), dict) and isinstance(raw.get("messages"), list)

    def _record_type(self, raw: dict[str, Any]) -> str:
        return str(raw.get("type") or raw.get("event") or "").lower()

    def _properties(self, raw: dict[str, Any]) -> dict[str, Any]:
        props = raw.get("properties")
        return dict(props) if isinstance(props, dict) else {}

    def _project_id(self, raw: dict[str, Any]) -> str:
        if raw.get("project_id") or raw.get("projectId") or raw.get("cwd") or raw.get("project"):
            return super()._project_id(raw)
        info = raw.get("info")
        if isinstance(info, dict):
            directory = info.get("directory")
            if isinstance(directory, str) and directory.strip():
                return directory
            project_id = info.get("projectID")
            if isinstance(project_id, str) and project_id.strip():
                return project_id
        message = raw.get("message")
        if isinstance(message, dict):
            path = message.get("path")
            if isinstance(path, dict):
                cwd = path.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    return cwd
        return super()._project_id(raw)

    def _session_id(self, raw: dict[str, Any]) -> str:
        if raw.get("sessionID"):
            return str(raw["sessionID"])
        props = self._properties(raw)
        if props.get("sessionID"):
            return str(props["sessionID"])
        message = raw.get("message")
        if isinstance(message, dict):
            info = message.get("info")
            if isinstance(info, dict) and info.get("sessionID"):
                return str(info["sessionID"])
        return super()._session_id(raw)

    def _extract_text_parts(self, parts: Iterable[Any]) -> str | None:
        values: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type in {"text", "reasoning"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    values.append(text)
        return "\n".join(values) if values else None

    def _export_message_content(self, info: dict[str, Any], parts: list[dict[str, Any]]) -> str | None:
        content = self._extract_text_parts(parts)
        if content:
            return content
        error = info.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict):
                message = data.get("message")
                if isinstance(message, str) and message.strip():
                    return message
            name = error.get("name")
            if isinstance(name, str) and name.strip():
                return name
        return None

    def _export_message_payload(self, info: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": "opencode_export",
            "message_id": info.get("id"),
            "agent": info.get("agent"),
        }
        if info.get("modelID") or info.get("providerID"):
            payload["model"] = {
                "providerID": info.get("providerID"),
                "modelID": info.get("modelID"),
            }
        file_touches = []
        summary = info.get("summary")
        if isinstance(summary, dict):
            for diff in summary.get("diffs") or []:
                if isinstance(diff, dict) and diff.get("file"):
                    file_touches.append(
                        {"path": str(diff["file"]), "operation": "edit"}
                    )
        if file_touches:
            payload["file_touches"] = file_touches
        if parts:
            payload["part_types"] = [part.get("type") for part in parts if isinstance(part, dict)]
        return payload

    def _hook_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"source": raw.get("_source") or "opencode_live"}
        if raw.get("callID"):
            payload["call_id"] = raw["callID"]
        args = raw.get("args")
        if isinstance(args, dict):
            payload["tool_input"] = args
        metadata = raw.get("metadata")
        if metadata not in (None, "", {}):
            payload["metadata"] = metadata
        file_touches = self._file_touches_from_tool(args if isinstance(args, dict) else None, metadata if isinstance(metadata, dict) else None)
        if file_touches:
            payload["file_touches"] = file_touches
        return payload

    def _file_touches_from_tool(
        self,
        tool_input: dict[str, Any] | None,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        raw: dict[str, Any] = {}
        if tool_input:
            raw["tool_input"] = tool_input
        if metadata:
            raw["metadata"] = metadata
        paths = list(dict.fromkeys(self._iter_paths(raw)))
        return [{"path": path, "operation": "edit"} for path in paths]

    def _tool_input_content(self, tool_input: Any) -> str | None:
        if isinstance(tool_input, dict):
            command = tool_input.get("command")
            if isinstance(command, list):
                return " ".join(str(part) for part in command)
            if isinstance(command, str) and command.strip():
                return command
            for key in ("prompt", "query", "url", "file", "filePath", "file_path", "path"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return self._trim(json.dumps(tool_input, sort_keys=True, default=str))
        if isinstance(tool_input, str) and tool_input.strip():
            return self._trim(tool_input)
        return None

    def _ts_from_ms(self, value: Any) -> str:
        if value is None:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            numeric = float(value) / 1000.0
        except (TypeError, ValueError):
            return self._ts({"ts": value})
        return datetime.fromtimestamp(numeric, UTC).isoformat().replace("+00:00", "Z")

    def _trim(self, value: str, *, limit: int = 16000) -> str:
        text = str(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…"
