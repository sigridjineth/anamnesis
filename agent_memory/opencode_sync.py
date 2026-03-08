from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from json import JSONDecoder
from pathlib import Path
from typing import Any, Iterable

from agent_memory.ingest import get_adapter
from agent_memory.storage import RawMemoryStore


def list_opencode_session_ids(*, limit: int | None = None) -> list[str]:
    result = subprocess.run(
        ["opencode", "session", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    session_ids: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Session ID") or set(stripped) == {"─"}:
            continue
        token = stripped.split()[0]
        if token.startswith("ses_"):
            session_ids.append(token)
            if limit is not None and len(session_ids) >= limit:
                break
    return session_ids


def parse_export_text(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        raise ValueError("OpenCode export did not contain a JSON object")
    payload, _ = JSONDecoder().raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("OpenCode export JSON must be an object")
    return payload


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


def iter_opencode_export_payloads(
    *,
    session_ids: Iterable[str] = (),
    export_files: Iterable[str | Path] = (),
    project_id: str | None = None,
) -> Iterable[dict[str, Any]]:
    for session_id in session_ids:
        payload = export_opencode_session(session_id)
        payload["_source"] = "opencode_export"
        if project_id and not payload.get("project_id"):
            payload["project_id"] = project_id
        yield payload
    for path in export_files:
        payload = load_export_file(path)
        payload["_source"] = "opencode_export"
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
    ) -> dict[str, Any]:
        adapter = get_adapter("opencode")
        payload_count = 0
        event_count = 0
        failures: list[dict[str, str]] = []

        for session_id in session_ids:
            try:
                payload = export_opencode_session(session_id)
            except Exception as exc:
                failures.append({"session_id": session_id, "error": str(exc)})
                continue
            payload["_source"] = "opencode_export"
            if project_id and not payload.get("project_id"):
                payload["project_id"] = project_id
            payload_count += 1
            event_count += self.store.append_payloads(adapter, [payload])["events"]

        for path in export_files:
            try:
                payload = load_export_file(path)
            except Exception as exc:
                failures.append({"path": str(Path(path).expanduser()), "error": str(exc)})
                continue
            payload["_source"] = "opencode_export"
            if project_id and not payload.get("project_id"):
                payload["project_id"] = project_id
            payload_count += 1
            event_count += self.store.append_payloads(adapter, [payload])["events"]

        return {
            "db_path": str(self.store.db_path),
            "payloads": payload_count,
            "events": event_count,
            "failures": failures,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill OpenCode exported sessions into the canonical raw memory store"
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
        help="Import every session visible to `opencode session list`.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of auto-discovered session ids when using --all-sessions or the default discovery mode.",
    )
    parser.add_argument("--project-id", help="Optional project identifier override")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    session_ids = list(args.session_id)
    if args.all_sessions or (not session_ids and not args.export_file):
        session_ids.extend(list_opencode_session_ids(limit=args.limit))

    service = OpenCodeSyncService(RawMemoryStore(args.db))
    summary = service.sync(
        session_ids=session_ids,
        export_files=args.export_file,
        project_id=args.project_id,
    )
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
