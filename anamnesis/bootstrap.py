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
            if self.config.opencode_storage_roots:
                session_ids = list_storage_session_ids(storage_roots=self.config.opencode_storage_roots)
            else:
                session_ids = list_opencode_session_ids(storage_roots=default_storage_roots())
            summaries["opencode"] = OpenCodeSyncService(store).sync(
                session_ids=session_ids,
                project_id=self.project_id,
                storage_roots=self.config.opencode_storage_roots,
                workspace_root=self.workspace_root,
                force_project_id=True,
            )
            summaries["opencode"]["discovered_session_ids"] = len(session_ids)

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize a workspace, import all local Claude/Codex/OpenCode history for that workspace, and rebuild the UQA sidecar."
    )
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--db-path")
    parser.add_argument("--clients", nargs="+", choices=DEFAULT_CLIENTS, default=list(DEFAULT_CLIENTS))
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--skip-register-codex", action="store_true")
    parser.add_argument("--skip-sidecar-rebuild", action="store_true")
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
