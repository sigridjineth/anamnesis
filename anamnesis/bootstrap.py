from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from anamnesis.claude_sync import ClaudeSyncService
from anamnesis.codex_sync import CodexSyncService, _default_history_path as _default_codex_history_path, _default_sessions_root as _default_codex_sessions_root
from anamnesis.config import Settings
from anamnesis.init_cli import DEFAULT_CLIENTS, InitConfig, InitService
from anamnesis.opencode_sync import (
    OpenCodeSyncService,
    default_storage_roots,
    list_opencode_session_ids,
    list_storage_session_ids,
    list_storage_session_ids_for_workspace,
)
from anamnesis.service import MemoryService
from anamnesis.storage import RawMemoryStore
from anamnesis.workspace_scope import normalize_workspace_root


@dataclass(slots=True)
class BootstrapConfig:
    workspace_root: Path
    python_executable: str
    db_path: Path
    clients: tuple[str, ...] = DEFAULT_CLIENTS
    codex_home: Path = field(default_factory=lambda: Path.home() / ".codex")
    register_codex: bool = True
    rebuild_sidecar: bool = True
    refresh_backfill: bool = False
    claude_history_path: Path = field(default_factory=lambda: Path.home() / ".claude" / "history.jsonl")
    claude_transcripts_root: Path = field(default_factory=lambda: Path.home() / ".claude" / "transcripts")
    claude_projects_root: Path = field(default_factory=lambda: Path.home() / ".claude" / "projects")
    codex_history_path: Path = field(default_factory=_default_codex_history_path)
    codex_sessions_root: Path = field(default_factory=_default_codex_sessions_root)
    opencode_storage_roots: tuple[Path, ...] = ()
    uqa_repo_root: Path | None = None


class BootstrapService:
    def __init__(self, config: BootstrapConfig):
        self.config = config
        self.workspace_root = normalize_workspace_root(config.workspace_root)
        self.project_id = str(self.workspace_root)

    def run(self) -> dict[str, Any]:
        self._log(f"Initializing workspace at {self.workspace_root}")
        init_summary = InitService(
            InitConfig(
                workspace_root=self.workspace_root,
                python_executable=self.config.python_executable,
                db_path=self.config.db_path,
                clients=self.config.clients,
                codex_home=self.config.codex_home,
                register_codex=self.config.register_codex,
                uqa_repo_root=self.config.uqa_repo_root,
            )
        ).run()

        store = RawMemoryStore(self.config.db_path)
        summaries: dict[str, Any] = {
            "init": init_summary,
        }
        bootstrap_state_path = self.workspace_root / ".anamnesis" / "bootstrap-state.json"
        performed_backfill = False
        skipped_existing = False
        if self.config.refresh_backfill:
            skip_backfill = False
        elif self._bootstrap_state_ready(bootstrap_state_path):
            skip_backfill = True
            skipped_existing = True
        elif self._can_adopt_existing_backfill(store):
            skip_backfill = True
            skipped_existing = True
            self._write_bootstrap_state(bootstrap_state_path, mode="adopted")
        else:
            skip_backfill = False

        if skip_backfill:
            self._log("Skipping historical backfill; workspace data is already initialized")
            for client in self.config.clients:
                summaries[client] = {
                    "skipped": True,
                    "reason": "historical backfill already completed for this workspace",
                }
        else:
            if "claude" in self.config.clients:
                self._log("Backfilling Claude history/transcripts/project index")
                summaries["claude"] = ClaudeSyncService(store).sync(
                    history_path=self.config.claude_history_path,
                    transcripts_root=self.config.claude_transcripts_root,
                    projects_root=self.config.claude_projects_root,
                    workspace_root=self.workspace_root,
                    project_id=self.project_id,
                    force_project_id=True,
                )
            if "codex" in self.config.clients:
                self._log("Backfilling Codex history and transcripts")
                summaries["codex"] = CodexSyncService(store).sync(
                    history_path=self.config.codex_history_path,
                    sessions_root=self.config.codex_sessions_root,
                    workspace_root=self.workspace_root,
                    project_id=self.project_id,
                    force_project_id=True,
                )

            if "opencode" in self.config.clients:
                self._log("Backfilling OpenCode sessions")
                storage_roots = self.config.opencode_storage_roots or tuple(default_storage_roots())
                if storage_roots:
                    session_ids = list_storage_session_ids_for_workspace(
                        self.workspace_root,
                        storage_roots=storage_roots,
                    )
                else:
                    session_ids = list_opencode_session_ids(storage_roots=default_storage_roots())
                summaries["opencode"] = OpenCodeSyncService(store).sync(
                    session_ids=session_ids,
                    project_id=self.project_id,
                    storage_roots=storage_roots,
                    workspace_root=self.workspace_root,
                    force_project_id=True,
                )
                summaries["opencode"]["discovered_session_ids"] = len(session_ids)
            performed_backfill = True
            self._write_bootstrap_state(bootstrap_state_path, mode="completed")

        sidecar_summary: dict[str, Any] | None = None
        if self.config.rebuild_sidecar:
            self._log("Rebuilding UQA sidecar")
            service = MemoryService(
                settings=Settings(
                    workspace_root=self.workspace_root,
                    raw_db_path=self.config.db_path,
                    uqa_sidecar_path=self.config.db_path.with_suffix(".uqa.db"),
                    uqa_repo_root=self.config.uqa_repo_root,
                )
            )
            sidecar_summary = service.rebuild_uqa_sidecar(db_path=str(self.config.db_path))

        counts = self._counts(self.config.db_path)
        return {
            "workspace_root": str(self.workspace_root),
            "project_id": self.project_id,
            "db_path": str(self.config.db_path),
            "uqa_sidecar_path": str(self.config.db_path.with_suffix(".uqa.db")),
            "clients": list(self.config.clients),
            "steps": summaries,
            "sidecar": sidecar_summary,
            "counts": counts,
            "bootstrap_state": {
                "path": str(bootstrap_state_path),
                "performed_backfill": performed_backfill,
                "skipped_existing": skipped_existing,
                "exists": bootstrap_state_path.exists(),
            },
        }

    def _counts(self, db_path: Path) -> dict[str, Any]:
        with sqlite3.connect(db_path) as conn:
            agent_counts = {
                agent: count
                for agent, count in conn.execute(
                    "SELECT agent, COUNT(*) FROM events GROUP BY agent ORDER BY agent"
                ).fetchall()
            }
            return {
                "events": int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                "sessions": int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
                "file_touches": int(conn.execute("SELECT COUNT(*) FROM file_touches").fetchone()[0]),
                "import_failures": int(conn.execute("SELECT COUNT(*) FROM import_failures").fetchone()[0]),
                "agents": agent_counts,
            }

    def _log(self, message: str) -> None:
        print(f"[anamnesis-bootstrap] {message}", file=sys.stderr)

    def _bootstrap_state_ready(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return (
            data.get("workspace_root") == str(self.workspace_root)
            and data.get("project_id") == self.project_id
            and data.get("db_path") == str(self.config.db_path)
            and data.get("status") in {"completed", "adopted"}
        )

    def _can_adopt_existing_backfill(self, store: RawMemoryStore) -> bool:
        requested = set(self.config.clients)
        if not requested:
            return False
        rows = store.fetchall(
            """
            SELECT agent, COUNT(*)
            FROM events
            WHERE project_id = ?
            GROUP BY agent
            """,
            (self.project_id,),
        )
        present = {str(agent) for agent, count in rows if int(count) > 0}
        return requested.issubset(present)

    def _write_bootstrap_state(self, path: Path, *, mode: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": mode,
            "workspace_root": str(self.workspace_root),
            "project_id": self.project_id,
            "db_path": str(self.config.db_path),
            "clients": list(self.config.clients),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize a workspace and backfill local Claude/Codex/OpenCode history for it. "
            "Repeated runs reuse the existing workspace bootstrap state unless --refresh-backfill is given."
        )
    )
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--db-path")
    parser.add_argument("--clients", nargs="+", choices=DEFAULT_CLIENTS, default=list(DEFAULT_CLIENTS))
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--skip-register-codex", action="store_true")
    parser.add_argument("--skip-sidecar-rebuild", action="store_true")
    parser.add_argument("--refresh-backfill", action="store_true")
    parser.add_argument("--claude-history", default=str(Path.home() / ".claude" / "history.jsonl"))
    parser.add_argument("--claude-transcripts-root", default=str(Path.home() / ".claude" / "transcripts"))
    parser.add_argument("--claude-projects-root", default=str(Path.home() / ".claude" / "projects"))
    parser.add_argument("--codex-history", default=str(_default_codex_history_path()))
    parser.add_argument("--codex-sessions-root", default=str(_default_codex_sessions_root()))
    parser.add_argument(
        "--opencode-storage-root",
        action="append",
        default=[],
        help="Optional OpenCode storage root to use. Repeatable.",
    )
    parser.add_argument("--uqa-repo-root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace_root = normalize_workspace_root(args.workspace_root)
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else workspace_root / ".anamnesis" / "anamnesis.db"
    config = BootstrapConfig(
        workspace_root=workspace_root,
        python_executable=args.python_executable,
        db_path=db_path,
        clients=tuple(args.clients),
        codex_home=Path(args.codex_home).expanduser().resolve(),
        register_codex=not args.skip_register_codex,
        rebuild_sidecar=not args.skip_sidecar_rebuild,
        refresh_backfill=args.refresh_backfill,
        claude_history_path=Path(args.claude_history).expanduser().resolve(),
        claude_transcripts_root=Path(args.claude_transcripts_root).expanduser().resolve(),
        claude_projects_root=Path(args.claude_projects_root).expanduser().resolve(),
        codex_history_path=Path(args.codex_history).expanduser().resolve(),
        codex_sessions_root=Path(args.codex_sessions_root).expanduser().resolve(),
        opencode_storage_roots=tuple(
            Path(root).expanduser().resolve() for root in (args.opencode_storage_root or ())
        ),
        uqa_repo_root=Path(args.uqa_repo_root).expanduser().resolve() if args.uqa_repo_root else None,
    )
    print(json.dumps(BootstrapService(config).run(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
