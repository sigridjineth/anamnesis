from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from anamnesis.ingest import get_adapter
from anamnesis.storage import RawMemoryStore
from anamnesis.workspace_scope import (
    apply_project_id,
    normalize_workspace_root,
    payload_mentions_workspace,
    workspace_contains_path,
)


def _default_history_path() -> Path:
    return Path.home() / ".claude" / "history.jsonl"


def _default_transcripts_root() -> Path:
    return Path.home() / ".claude" / "transcripts"


def _default_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _claude_project_dir_name(workspace_root: str | Path) -> str:
    return str(normalize_workspace_root(workspace_root)).replace("/", "-")


def _project_index_path(projects_root: str | Path | None, workspace_root: str | Path) -> Path:
    root = Path(projects_root or _default_projects_root()).expanduser().resolve()
    return root / _claude_project_dir_name(workspace_root) / "sessions-index.json"


def _derived_history_session_id(raw: dict[str, Any], *, line_number: int) -> str:
    source = "|".join(
        [
            str(raw.get("project") or ""),
            str(raw.get("timestamp") or ""),
            str(raw.get("display") or ""),
            str(line_number),
        ]
    )
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()
    return f"claude-history:{digest[:24]}"


def iter_claude_history_payloads(
    path: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
    project_id: str | None = None,
    force_project_id: bool = False,
) -> Iterable[dict[str, Any]]:
    history_path = Path(path or _default_history_path()).expanduser()
    scope = normalize_workspace_root(workspace_root) if workspace_root else None
    if not history_path.exists():
        return
    with history_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            project = raw.get("project")
            if scope is not None and not workspace_contains_path(project, scope):
                continue
            payload = dict(raw)
            payload["_source"] = "claude_history"
            payload["event"] = "UserPromptSubmit"
            payload["prompt"] = raw.get("display")
            payload["session_id"] = raw.get("sessionId") or raw.get("session_id") or _derived_history_session_id(
                raw, line_number=line_number
            )
            yield apply_project_id(payload, project_id, force=force_project_id)


def iter_claude_project_session_payloads(
    *,
    projects_root: str | Path | None = None,
    workspace_root: str | Path,
    project_id: str | None = None,
    force_project_id: bool = False,
) -> Iterable[dict[str, Any]]:
    index_path = _project_index_path(projects_root, workspace_root)
    scope = normalize_workspace_root(workspace_root)
    if not index_path.exists():
        return
    data = json.loads(index_path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not workspace_contains_path(entry.get("projectPath"), scope):
            continue
        payload = {
            **entry,
            "_source": "claude_project_index",
            "event": "SessionIndexed",
            "session_id": entry.get("sessionId") or entry.get("session_id"),
            "timestamp": entry.get("created") or entry.get("modified") or entry.get("fileMtime"),
            "content": entry.get("firstPrompt") or entry.get("gitBranch") or entry.get("fullPath"),
        }
        yield apply_project_id(payload, project_id, force=force_project_id)


@dataclass(slots=True)
class ClaudeSyncService:
    store: RawMemoryStore
    batch_size: int = 200

    def sync(
        self,
        *,
        history_path: str | Path | None = None,
        transcripts_root: str | Path | None = None,
        projects_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
        project_id: str | None = None,
        include_history: bool = True,
        include_transcripts: bool = True,
        include_project_index: bool = True,
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
            "project_index": {
                "path": str(_project_index_path(projects_root, workspace_root)) if workspace_root else None,
                "payloads": 0,
                "events": 0,
            },
            "transcripts": {
                "root": str(Path(transcripts_root or _default_transcripts_root()).expanduser()),
                "files": 0,
                "matched_files": 0,
                "payloads": 0,
                "events": 0,
                "failures": [],
            },
        }
        matched_session_ids: set[str] = set()

        if include_history:
            summary["history"] = {
                "path": str(Path(history_path or _default_history_path()).expanduser()),
                **self._ingest_payloads(
                    iter_claude_history_payloads(
                        history_path,
                        workspace_root=workspace_root,
                        project_id=canonical_project_id,
                        force_project_id=force_project_id,
                    ),
                    matched_session_ids=matched_session_ids,
                ),
            }

        if include_project_index and workspace_root is not None:
            summary["project_index"] = {
                "path": str(_project_index_path(projects_root, workspace_root)),
                **self._ingest_payloads(
                    iter_claude_project_session_payloads(
                        projects_root=projects_root,
                        workspace_root=workspace_root,
                        project_id=canonical_project_id,
                        force_project_id=force_project_id,
                    ),
                    matched_session_ids=matched_session_ids,
                ),
            }

        if include_transcripts:
            summary["transcripts"] = self._sync_transcripts(
                transcripts_root=transcripts_root,
                workspace_root=workspace_root,
                project_id=canonical_project_id,
                force_project_id=force_project_id,
                matched_session_ids=matched_session_ids,
            )

        return summary

    def _ingest_payloads(
        self,
        payloads: Iterable[dict[str, Any]],
        *,
        matched_session_ids: set[str] | None = None,
    ) -> dict[str, int]:
        adapter = get_adapter("claude")
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

    def _sync_transcripts(
        self,
        *,
        transcripts_root: str | Path | None = None,
        workspace_root: str | Path | None = None,
        project_id: str | None = None,
        force_project_id: bool = False,
        matched_session_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        root = Path(transcripts_root or _default_transcripts_root()).expanduser()
        scope = normalize_workspace_root(workspace_root) if workspace_root else None
        adapter = get_adapter("claude")
        payload_count = 0
        event_count = 0
        file_count = 0
        matched_files = 0
        failures: list[dict[str, str]] = []

        if not root.exists():
            return {
                "root": str(root),
                "files": 0,
                "matched_files": 0,
                "payloads": 0,
                "events": 0,
                "failures": [],
            }

        paths = _transcript_paths(root, matched_session_ids)
        for path in paths:
            file_count += 1
            payloads: list[dict[str, Any]] = []
            matched = scope is None
            try:
                with path.open(encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError as exc:
                            failure = {"path": str(path), "line": str(line_number), "error": str(exc)}
                            failures.append(failure)
                            self.store.record_import_failure(
                                agent="claude",
                                source="transcript_line",
                                ref=f"{path}:{line_number}",
                                error=str(exc),
                                raw_excerpt=line[:500],
                            )
                            continue
                        if not isinstance(raw, dict):
                            continue
                        if scope is not None and payload_mentions_workspace(raw, scope):
                            matched = True
                        payload = {
                            **raw,
                            "_source": "claude_transcript",
                            "session_id": f"claude-transcript:{path.stem}",
                        }
                        payload = apply_project_id(payload, project_id, force=force_project_id)
                        payloads.append(payload)
            except OSError as exc:
                failures.append({"path": str(path), "error": str(exc)})
                self.store.record_import_failure(
                    agent="claude",
                    source="transcript_file",
                    ref=str(path),
                    error=str(exc),
                )
                continue

            if not payloads or not matched:
                continue
            matched_files += 1
            payload_count += len(payloads)
            event_count += self.store.append_payloads(adapter, payloads)["events"]

        return {
            "root": str(root),
            "files": file_count,
            "matched_files": matched_files,
            "payloads": payload_count,
            "events": event_count,
            "failures": failures,
        }


def _transcript_paths(root: Path, matched_session_ids: set[str] | None) -> list[Path]:
    if not matched_session_ids:
        return sorted(root.rglob("*.jsonl"))
    candidates: list[Path] = []
    seen: set[Path] = set()
    for session_id in sorted(matched_session_ids):
        direct = root / f"{session_id}.jsonl"
        if direct.exists() and direct not in seen:
            seen.add(direct)
            candidates.append(direct)
            continue
        for path in root.rglob(f"{session_id}.jsonl"):
            if path not in seen:
                seen.add(path)
                candidates.append(path)
                break
    if candidates and len(candidates) >= len(matched_session_ids):
        return candidates
    return sorted(root.rglob("*.jsonl"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Claude Code history, project session index, and transcripts into the canonical raw memory store"
    )
    parser.add_argument("--db", required=True, help="Path to the raw memory SQLite database")
    parser.add_argument("--workspace-root", help="Only import Claude artifacts relevant to this workspace root")
    parser.add_argument("--history", default=str(_default_history_path()))
    parser.add_argument("--transcripts-root", default=str(_default_transcripts_root()))
    parser.add_argument("--projects-root", default=str(_default_projects_root()))
    parser.add_argument("--project-id", help="Optional canonical project identifier to write onto imported rows")
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument("--skip-transcripts", action="store_true")
    parser.add_argument("--skip-project-index", action="store_true")
    parser.add_argument(
        "--force-project-id",
        action="store_true",
        help="Always overwrite project_id on imported Claude payloads instead of only filling missing values.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    service = ClaudeSyncService(RawMemoryStore(args.db))
    summary = service.sync(
        history_path=args.history,
        transcripts_root=args.transcripts_root,
        projects_root=args.projects_root,
        workspace_root=args.workspace_root,
        project_id=args.project_id,
        include_history=not args.skip_history,
        include_transcripts=not args.skip_transcripts,
        include_project_index=not args.skip_project_index,
        force_project_id=args.force_project_id,
    )
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
