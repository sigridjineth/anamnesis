from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from anamnesis.ingest import get_adapter
from anamnesis.storage import RawMemoryStore
from anamnesis.workspace_scope import apply_project_id, normalize_workspace_root, payload_mentions_workspace


def _default_history_path() -> Path:
    return Path.home() / ".codex" / "history.jsonl"


def _default_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _item_timestamp(session_ts: str | None, index: int) -> str:
    base = _parse_iso(session_ts)
    if base is None:
        base = datetime(1970, 1, 1, tzinfo=UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    value = base + timedelta(milliseconds=index)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iter_codex_history_payloads(
    path: str | Path | None = None,
    *,
    project_id: str | None = None,
    workspace_root: str | Path | None = None,
    force_project_id: bool = False,
) -> Iterable[dict[str, Any]]:
    history_path = Path(path or _default_history_path()).expanduser()
    scope = normalize_workspace_root(workspace_root) if workspace_root else None
    if not history_path.exists():
        return
    with history_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            if scope is not None and not payload_mentions_workspace(raw, scope):
                continue
            payload = dict(raw)
            payload["_source"] = "codex_history"
            yield apply_project_id(payload, project_id, force=force_project_id)


def iter_codex_session_payloads(
    root: str | Path | None = None,
    *,
    project_id: str | None = None,
    include_user_messages: bool = False,
    workspace_root: str | Path | None = None,
    matched_session_ids: set[str] | None = None,
    force_project_id: bool = False,
) -> Iterable[dict[str, Any]]:
    sessions_root = Path(root or _default_sessions_root()).expanduser()
    scope = normalize_workspace_root(workspace_root) if workspace_root else None
    matched_session_ids = matched_session_ids or set()
    if not sessions_root.exists():
        return

    for path in sorted(sessions_root.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        session = data.get("session")
        if not isinstance(session, dict):
            session = {}
        session_id = str(session.get("id") or path.stem)
        if scope is not None and session_id not in matched_session_ids and not payload_mentions_workspace(data, scope):
            continue
        session_ts = session.get("timestamp")
        call_lookup: dict[str, dict[str, Any]] = {}
        items = data.get("items")
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if item_type == "reasoning":
                continue
            if (
                item_type == "message"
                and str(item.get("role") or "").lower() == "user"
                and not include_user_messages
            ):
                continue
            payload = dict(item)
            payload["session_id"] = session_id
            payload["ts"] = _item_timestamp(session_ts, index)
            payload["_source"] = "codex_session"
            payload["_item_index"] = index
            payload["_session_timestamp"] = session_ts
            payload = apply_project_id(payload, project_id, force=force_project_id)
            if item_type == "function_call":
                tool_input = _maybe_json(payload.get("arguments"))
                call_id = str(payload.get("call_id") or "")
                if call_id:
                    call_lookup[call_id] = {
                        "tool_name": payload.get("name"),
                        "tool_input": tool_input,
                    }
                if tool_input not in (None, "", {}):
                    payload["tool_input"] = tool_input
                if payload.get("name"):
                    payload["tool_name"] = payload["name"]
            elif item_type == "function_call_output":
                linked = call_lookup.get(str(payload.get("call_id") or ""))
                if linked:
                    payload.update({k: v for k, v in linked.items() if v not in (None, "", {})})
            yield payload


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return value


@dataclass(slots=True)
class CodexSyncService:
    store: RawMemoryStore
    batch_size: int = 200

    def sync(
        self,
        *,
        history_path: str | Path | None = None,
        sessions_root: str | Path | None = None,
        project_id: str | None = None,
        include_history: bool = True,
        include_sessions: bool = True,
        include_user_messages: bool = False,
        workspace_root: str | Path | None = None,
        force_project_id: bool = False,
    ) -> dict[str, Any]:
        canonical_project_id = project_id
        if workspace_root is not None and canonical_project_id is None:
            canonical_project_id = str(normalize_workspace_root(workspace_root))
            force_project_id = True
        summary: dict[str, Any] = {
            "db_path": str(self.store.db_path),
            "workspace_root": str(normalize_workspace_root(workspace_root)) if workspace_root else None,
            "history": {"path": str(Path(history_path or _default_history_path()).expanduser()), "payloads": 0, "events": 0},
            "sessions": {"root": str(Path(sessions_root or _default_sessions_root()).expanduser()), "payloads": 0, "events": 0},
        }
        matched_session_ids: set[str] = set()
        if include_history:
            history_payloads = iter_codex_history_payloads(
                history_path,
                project_id=canonical_project_id,
                workspace_root=workspace_root,
                force_project_id=force_project_id,
            )
            summary["history"] = {
                "path": str(Path(history_path or _default_history_path()).expanduser()),
                **self._ingest_payloads(
                    history_payloads,
                    matched_session_ids=matched_session_ids,
                ),
            }
        if include_sessions:
            summary["sessions"] = {
                "root": str(Path(sessions_root or _default_sessions_root()).expanduser()),
                **self._ingest_payloads(
                    iter_codex_session_payloads(
                        sessions_root,
                        project_id=canonical_project_id,
                        include_user_messages=include_user_messages,
                        workspace_root=workspace_root,
                        matched_session_ids=matched_session_ids,
                        force_project_id=force_project_id,
                    )
                ),
            }
        return summary

    def _ingest_payloads(
        self,
        payloads: Iterable[dict[str, Any]],
        *,
        matched_session_ids: set[str] | None = None,
    ) -> dict[str, int]:
        adapter = get_adapter("codex")
        payload_count = 0
        event_count = 0
        batch: list[dict[str, Any]] = []
        for payload in payloads:
            payload_count += 1
            if matched_session_ids is not None:
                session_id = payload.get("session_id") or payload.get("sessionId") or payload.get("session")
                if isinstance(session_id, str) and session_id.strip():
                    matched_session_ids.add(session_id)
            batch.append(payload)
            if len(batch) >= self.batch_size:
                result = self.store.append_payloads(adapter, batch)
                event_count += result["events"]
                batch.clear()
        if batch:
            result = self.store.append_payloads(adapter, batch)
            event_count += result["events"]
        return {"payloads": payload_count, "events": event_count}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Codex history and session transcripts into the canonical raw memory store"
    )
    parser.add_argument("--db", required=True, help="Path to the raw memory SQLite database")
    parser.add_argument("--history", default=str(_default_history_path()))
    parser.add_argument("--sessions-root", default=str(_default_sessions_root()))
    parser.add_argument("--project-id", help="Optional project identifier override")
    parser.add_argument("--workspace-root", help="Only import Codex artifacts relevant to this workspace root")
    parser.add_argument(
        "--force-project-id",
        action="store_true",
        help="Always overwrite project_id on imported Codex payloads instead of only filling missing values.",
    )
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument("--skip-sessions", action="store_true")
    parser.add_argument(
        "--include-user-messages",
        action="store_true",
        help="Also import user messages from session transcripts. Disabled by default to avoid duplicating history.jsonl prompts.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    service = CodexSyncService(RawMemoryStore(args.db))
    summary = service.sync(
        history_path=args.history,
        sessions_root=args.sessions_root,
        project_id=args.project_id,
        include_history=not args.skip_history,
        include_sessions=not args.skip_sessions,
        include_user_messages=args.include_user_messages,
        workspace_root=args.workspace_root,
        force_project_id=args.force_project_id,
    )
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
