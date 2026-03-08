from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anamnesis.discovery import discover_workspace_root, workspace_db_path
from anamnesis.ingest import IngestionService, apply_overrides, load_payloads
from anamnesis.storage import RawMemoryStore

from anamnesis.ingest import main as ingest_main


def run(agent: str) -> None:
    ingest_main(default_agent=agent)


def run_codex() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize Codex hook payloads and append them to workspace-local raw memory stores"
    )
    parser.add_argument("--db", help="Optional explicit raw memory SQLite database path")
    parser.add_argument("--input", help="Optional path to a JSON/JSONL payload file. Reads stdin when omitted.")
    parser.add_argument("--session-id")
    parser.add_argument("--project-id")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    text = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    payloads = apply_overrides(
        load_payloads(text),
        session_id=args.session_id,
        project_id=args.project_id,
    )

    if args.db:
        summary = IngestionService(RawMemoryStore(args.db)).ingest("codex", payloads)
    else:
        grouped: dict[str, list[dict[str, object]]] = {}
        for payload in payloads:
            cwd = payload.get("cwd") or payload.get("project") or payload.get("project_id") or payload.get("projectId")
            workspace_root = discover_workspace_root(cwd)
            item = dict(payload)
            item["project_id"] = str(workspace_root)
            db_path = workspace_db_path(workspace_root)
            grouped.setdefault(str(db_path), []).append(item)

        summary = {
            "agent": "codex",
            "targets": [],
            "payloads": len(payloads),
            "events": 0,
        }
        for db_path, batch in grouped.items():
            result = IngestionService(RawMemoryStore(db_path)).ingest("codex", batch)
            summary["targets"].append(
                {
                    "db_path": db_path,
                    "payloads": result["payloads"],
                    "events": result["events"],
                    "workspace_root": str(Path(db_path).parent.parent.resolve()),
                }
            )
            summary["events"] += result["events"]

    if not args.quiet:
        print(json.dumps(summary, indent=2))
