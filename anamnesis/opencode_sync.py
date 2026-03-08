from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from anamnesis.ingest import get_adapter
from anamnesis.storage import RawMemoryStore


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def default_storage_roots() -> list[Path]:
    roots = [Path.home() / ".local" / "share" / "opencode" / "storage"]
    return [root.expanduser().resolve() for root in roots if root.expanduser().exists()]


def list_storage_session_ids(*, storage_roots: Sequence[str | Path] = (), limit: int | None = None) -> list[str]:
    session_ids: list[str] = []
    seen: set[str] = set()
    for root in _resolved_storage_roots(storage_roots):
        session_dir = root / "session"
        if not session_dir.exists():
            continue
        for path in sorted(session_dir.rglob("ses_*.json")):
            session_id = path.stem
            if session_id in seen:
                continue
            seen.add(session_id)
            session_ids.append(session_id)
            if limit is not None and len(session_ids) >= limit:
                return session_ids
    return session_ids


def list_opencode_session_ids(*, limit: int | None = None, storage_roots: Sequence[str | Path] = ()) -> list[str]:
    try:
        result = subprocess.run(
            ["opencode", "session", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return list_storage_session_ids(storage_roots=storage_roots, limit=limit)

    session_ids: list[str] = []
    for line in result.stdout.splitlines():
        stripped = _strip_ansi(line).strip()
        if not stripped or stripped.startswith("Session ID") or set(stripped) == {"─"}:
            continue
        token = stripped.split()[0]
        if token.startswith("ses_"):
            session_ids.append(token)
            if limit is not None and len(session_ids) >= limit:
                return session_ids
    if session_ids:
        return session_ids
    return list_storage_session_ids(storage_roots=storage_roots, limit=limit)


def parse_export_text(text: str) -> dict[str, Any]:
    stripped = _strip_ansi(text).strip()
    if not stripped:
        raise ValueError("OpenCode export was empty")

    payload = _coerce_export_payload(_try_load_json(stripped))
    if payload is not None:
        return payload

    objects = list(_scan_json_objects(stripped))
    payload = _coerce_export_payload(objects)
    if payload is not None:
        return payload

    raise ValueError("OpenCode export did not contain a recoverable session document")


def load_export_file(path: str | Path) -> dict[str, Any]:
    return parse_export_text(Path(path).expanduser().read_text(encoding="utf-8"))


def export_opencode_session(session_id: str) -> dict[str, Any]:
    result = subprocess.run(
        ["opencode", "export", session_id],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_export_text(result.stdout)


def load_storage_session(session_id: str, *, storage_roots: Sequence[str | Path] = ()) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for root in _resolved_storage_roots(storage_roots):
        session_file = _find_storage_file(root / "session", session_id)
        if session_file is None:
            continue
        info = _safe_load_json(session_file, failures, kind="session")
        if info is None:
            continue
        message_dir = root / "message" / session_id
        messages: list[dict[str, Any]] = []
        if message_dir.exists():
            for message_path in sorted(message_dir.glob("*.json"), key=_message_sort_key):
                message_info = _safe_load_json(message_path, failures, kind="message")
                if message_info is None:
                    continue
                message_id = str(message_info.get("id") or "")
                parts = []
                if message_id:
                    part_dir = root / "part" / message_id
                    if part_dir.exists():
                        for part_path in sorted(part_dir.glob("*.json"), key=_part_sort_key):
                            part_info = _safe_load_json(part_path, failures, kind="part")
                            if part_info is not None:
                                parts.append(part_info)
                messages.append({"info": message_info, "parts": parts})
        payload = {"info": info, "messages": messages, "_source": "opencode_storage"}
        session_diff_file = root / "session_diff" / f"{session_id}.json"
        if session_diff_file.exists():
            try:
                payload["diffs"] = _load_json(session_diff_file)
            except Exception:
                failures.append({"path": str(session_diff_file), "error": "failed to parse session diff"})
        if failures:
            payload["_import_failures"] = failures
        return payload
    raise FileNotFoundError(f"OpenCode storage session not found: {session_id}")


def iter_opencode_export_payloads(
    *,
    session_ids: Iterable[str] = (),
    export_files: Iterable[str | Path] = (),
    project_id: str | None = None,
    storage_roots: Sequence[str | Path] = (),
) -> Iterable[dict[str, Any]]:
    for session_id in session_ids:
        payload = _load_session_payload(session_id, storage_roots=storage_roots)
        if project_id and not payload.get("project_id"):
            payload["project_id"] = project_id
        yield payload
    for path in export_files:
        payload = load_export_file(path)
        payload["_source"] = payload.get("_source") or "opencode_export"
        if project_id and not payload.get("project_id"):
            payload["project_id"] = project_id
        yield payload


@dataclass(slots=True)
class OpenCodeSyncService:
    store: RawMemoryStore

    def sync(
        self,
        *,
        session_ids: Iterable[str] = (),
        export_files: Iterable[str | Path] = (),
        project_id: str | None = None,
        storage_roots: Sequence[str | Path] = (),
    ) -> dict[str, Any]:
        adapter = get_adapter("opencode")
        payload_count = 0
        event_count = 0
        failures: list[dict[str, str]] = []
        fallbacks: list[dict[str, str]] = []

        for session_id in session_ids:
            try:
                payload = _load_session_payload(session_id, storage_roots=storage_roots, fallbacks=fallbacks)
            except Exception as exc:
                self.store.record_import_failure(
                    agent="opencode",
                    source="session_id",
                    ref=session_id,
                    error=str(exc),
                )
                failures.append({"session_id": session_id, "error": str(exc)})
                continue
            if project_id and not payload.get("project_id"):
                payload["project_id"] = project_id
            for failure in payload.pop("_import_failures", []):
                self.store.record_import_failure(
                    agent="opencode",
                    source="storage_file",
                    ref=failure.get("path"),
                    error=failure.get("error") or "failed to parse storage file",
                )
            payload_count += 1
            event_count += self.store.append_payloads(adapter, [payload])["events"]

        for path in export_files:
            try:
                payload = load_export_file(path)
            except Exception as exc:
                resolved = str(Path(path).expanduser())
                self.store.record_import_failure(agent="opencode", source="export_file", ref=resolved, error=str(exc))
                failures.append({"path": resolved, "error": str(exc)})
                continue
            payload["_source"] = payload.get("_source") or "opencode_export"
            if project_id and not payload.get("project_id"):
                payload["project_id"] = project_id
            payload_count += 1
            event_count += self.store.append_payloads(adapter, [payload])["events"]

        return {
            "db_path": str(self.store.db_path),
            "payloads": payload_count,
            "events": event_count,
            "failures": failures,
            "fallbacks": fallbacks,
        }


def _load_session_payload(
    session_id: str,
    *,
    storage_roots: Sequence[str | Path] = (),
    fallbacks: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        payload = export_opencode_session(session_id)
        payload["_source"] = payload.get("_source") or "opencode_export"
        return payload
    except Exception as exc:
        payload = load_storage_session(session_id, storage_roots=storage_roots)
        if fallbacks is not None:
            fallbacks.append({"session_id": session_id, "source": "opencode_storage", "reason": str(exc)})
        return payload


def _try_load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except JSONDecodeError:
        pass
    start = min(
        [index for index in (text.find("{"), text.find("[")) if index >= 0],
        default=-1,
    )
    if start < 0:
        return None
    try:
        payload, _ = JSONDecoder().raw_decode(text[start:])
    except JSONDecodeError:
        return None
    return payload


def _scan_json_objects(text: str) -> Iterable[Any]:
    decoder = JSONDecoder()
    for index, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except JSONDecodeError:
            continue
        yield payload


def _coerce_export_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if _is_export_document(payload):
            return payload
        merged = _merge_export_fragments([payload])
        return merged
    if isinstance(payload, list):
        merged = _merge_export_fragments(payload)
        return merged
    return None


def _merge_export_fragments(objects: Iterable[Any]) -> dict[str, Any] | None:
    info: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = []

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        if _is_export_document(obj):
            if info is None and isinstance(obj.get("info"), dict):
                info = dict(obj["info"])
            messages.extend(_message_dicts(obj.get("messages")))
            continue
        if _looks_like_export_info(obj):
            info = dict(obj)
            continue
        if isinstance(obj.get("info"), dict) and _looks_like_export_info(obj["info"]):
            if info is None:
                info = dict(obj["info"])
        if _looks_like_message(obj):
            messages.append(dict(obj))
            continue
        if isinstance(obj.get("messages"), list):
            messages.extend(_message_dicts(obj.get("messages")))

    if info is None and not messages:
        return None
    if info is None:
        session_id = _first_session_id(messages) or "unknown-session"
        info = {
            "id": session_id,
            "title": f"Recovered session {session_id}",
            "time": {"created": None},
        }
    payload = {"info": info, "messages": messages}
    if not _is_export_document(payload):
        return None
    return payload


def _is_export_document(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("info"), dict) and isinstance(payload.get("messages"), list)


def _looks_like_export_info(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("id", "directory", "projectID", "slug", "title", "version"))


def _looks_like_message(payload: dict[str, Any]) -> bool:
    info = payload.get("info")
    parts = payload.get("parts")
    return isinstance(info, dict) and isinstance(parts, list)


def _message_dicts(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    return [dict(message) for message in messages if isinstance(message, dict) and _looks_like_message(message)]


def _first_session_id(messages: Iterable[dict[str, Any]]) -> str | None:
    for message in messages:
        info = message.get("info")
        if not isinstance(info, dict):
            continue
        session_id = info.get("sessionID")
        if isinstance(session_id, str) and session_id.strip():
            return session_id
    return None


def _resolved_storage_roots(storage_roots: Sequence[str | Path]) -> list[Path]:
    if storage_roots:
        roots = [Path(root).expanduser().resolve() for root in storage_roots]
    else:
        roots = default_storage_roots()
    seen: set[Path] = set()
    result: list[Path] = []
    for root in roots:
        if root in seen or not root.exists():
            continue
        seen.add(root)
        result.append(root)
    return result


def _find_storage_file(root: Path, stem: str) -> Path | None:
    if not root.exists():
        return None
    direct = root / f"{stem}.json"
    if direct.exists():
        return direct
    for path in root.rglob(f"{stem}.json"):
        return path
    return None


def _load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except JSONDecodeError:
        sanitized = _sanitize_json(text)
        return json.loads(sanitized)


def _safe_load_json(
    path: Path,
    failures: list[dict[str, str]],
    *,
    kind: str,
) -> Any | None:
    try:
        return _load_json(path)
    except Exception as exc:
        failures.append({"path": str(path), "error": f"{kind}: {exc}"})
        return None


def _message_sort_key(path: Path) -> tuple[int, str]:
    try:
        data = _load_json(path)
    except Exception:
        return (0, path.name)
    return (_time_value(data.get("time"), "created"), path.name)


def _part_sort_key(path: Path) -> tuple[int, str]:
    try:
        data = _load_json(path)
    except Exception:
        return (0, path.name)
    time_info = data.get("time")
    return (
        _time_value(time_info, "start")
        or _time_value(time_info, "created")
        or _time_value(time_info, "end"),
        path.name,
    )


def _time_value(value: Any, key: str) -> int:
    if not isinstance(value, dict):
        return 0
    raw = value.get(key)
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def _sanitize_json(text: str) -> str:
    stripped = _strip_ansi(text).strip()
    start = min([index for index in (stripped.find("{"), stripped.find("[")) if index >= 0], default=-1)
    if start > 0:
        stripped = stripped[start:]
    end_object = stripped.rfind("}")
    end_array = stripped.rfind("]")
    end = max(end_object, end_array)
    if end >= 0:
        stripped = stripped[: end + 1]
    return stripped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill OpenCode exported sessions or local storage sessions into the canonical raw memory store"
    )
    parser.add_argument("--db", required=True, help="Path to the raw memory SQLite database")
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="OpenCode session id to export and import. Repeatable.",
    )
    parser.add_argument(
        "--export-file",
        action="append",
        default=[],
        help="Path to an already-exported OpenCode session JSON file. Repeatable.",
    )
    parser.add_argument(
        "--all-sessions",
        action="store_true",
        help="Import every session visible to `opencode session list`, falling back to local storage when needed.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of auto-discovered session ids when using --all-sessions or the default discovery mode.",
    )
    parser.add_argument(
        "--storage-root",
        action="append",
        default=[],
        help="Optional OpenCode storage root to use for discovery/fallback import. Repeatable.",
    )
    parser.add_argument("--project-id", help="Optional project identifier override")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    session_ids = list(args.session_id)
    if args.all_sessions or (not session_ids and not args.export_file):
        session_ids.extend(list_opencode_session_ids(limit=args.limit, storage_roots=args.storage_root))

    service = OpenCodeSyncService(RawMemoryStore(args.db))
    summary = service.sync(
        session_ids=session_ids,
        export_files=args.export_file,
        project_id=args.project_id,
        storage_roots=args.storage_root,
    )
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
