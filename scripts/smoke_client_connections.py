#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd or REPO_ROOT,
        env=env,
        input=input_text,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_hook_command(settings: dict[str, Any], event_name: str) -> str:
    hooks = settings.get("hooks", {})
    blocks = hooks.get(event_name, [])
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for hook in block.get("hooks", []):
            if isinstance(hook, dict) and hook.get("type") == "command" and hook.get("command"):
                return str(hook["command"])
    raise KeyError(f"No command hook found for {event_name}")


def _run_shell_command(command: str, *, cwd: Path, env: dict[str, str] | None = None, input_text: str | None = None) -> None:
    _run(["bash", "-lc", command], cwd=cwd, env=env, input_text=input_text)


def _sqlite_counts(db_path: Path) -> tuple[dict[str, int], int]:
    with sqlite3.connect(db_path) as conn:
        agent_counts = {
            agent: count
            for agent, count in conn.execute(
                "SELECT agent, COUNT(*) FROM events GROUP BY agent ORDER BY agent"
            ).fetchall()
        }
        session_count = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    return agent_counts, session_count


def _ingest_claude(workspace_root: Path, db_path: Path) -> None:
    settings = _load_json(workspace_root / ".claude" / "settings.local.json")
    command = _first_hook_command(settings, "UserPromptSubmit")
    payload = {
        "event": "UserPromptSubmit",
        "session_id": "claude-session-1",
        "project": str(workspace_root),
        "timestamp": "2026-03-08T00:00:00Z",
        "prompt": "Map the install script history",
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(workspace_root)
    _run_shell_command(command, cwd=workspace_root, env=env, input_text=json.dumps(payload))
    if not db_path.exists():
        raise AssertionError("Claude hook did not create the raw database")


def _ingest_codex(codex_home: Path, workspace_root: Path) -> None:
    settings = _load_json(codex_home / "settings.json")
    command = _first_hook_command(settings, "UserPromptSubmit")
    payload = {
        "tool": "UserPrompt",
        "session": "codex-session-1",
        "cwd": str(workspace_root),
        "ts": 1772932800,
        "prompt": "Review deployment architecture",
    }
    _run_shell_command(command, cwd=workspace_root, input_text=json.dumps(payload))


def _ingest_opencode(python_executable: str, workspace_root: Path, db_path: Path) -> None:
    plugin_text = (workspace_root / ".opencode" / "plugins" / "anamnesis.ts").read_text(encoding="utf-8")
    for marker in ("chat.message", "tool.execute.before", "session.updated", "anamnesis.hooks.opencode"):
        if marker not in plugin_text:
            raise AssertionError(f"OpenCode plugin missing marker: {marker}")

    payload = {
        "type": "chat.message",
        "sessionID": "opencode-session-1",
        "projectId": str(workspace_root),
        "message": {
            "id": "msg-1",
            "sessionID": "opencode-session-1",
            "role": "user",
        },
        "parts": [
            {"type": "text", "text": "Summarize cross-tool memory state"},
        ],
    }
    _run(
        [python_executable, "-m", "anamnesis.hooks.opencode", "--db", str(db_path), "--quiet"],
        cwd=workspace_root,
        input_text=json.dumps(payload),
    )


def _query_surface(python_executable: str, workspace_root: Path, db_path: Path) -> dict[str, Any]:
    survey = json.loads(
        _run(
            [python_executable, "-m", "anamnesis", "search", "@survey", "--db", str(db_path)],
            cwd=workspace_root,
        ).stdout
    )
    chronicle = json.loads(
        _run(
            [
                python_executable,
                "-m",
                "anamnesis",
                "search",
                "@chronicle session=claude-session-1 limit=5",
                "--db",
                str(db_path),
            ],
            cwd=workspace_root,
        ).stdout
    )
    synopsis = json.loads(
        _run(
            [
                python_executable,
                "-m",
                "anamnesis",
                "search",
                "@synopsis days=30",
                "--db",
                str(db_path),
            ],
            cwd=workspace_root,
        ).stdout
    )
    return {
        "survey": {
            "backend": survey["backend"],
            "macros": survey["macros"],
            "counts": survey["counts"],
        },
        "chronicle": {
            "session_id": chronicle["session"]["session_id"],
            "timeline_count": len(chronicle["timeline"]),
        },
        "synopsis": {
            "days": synopsis["days"],
            "session_count": len(synopsis["sessions"]),
            "top_file_count": len(synopsis["top_files"]),
        },
    }


def smoke_client_connections(*, workspace_root: Path, python_executable: str) -> dict[str, Any]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    db_path = workspace_root / ".anamnesis" / "anamnesis.db"
    codex_home = workspace_root / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)

    _run(
        [
            python_executable,
            "-m",
            "anamnesis.init_cli",
            "--workspace-root",
            str(workspace_root),
            "--db-path",
            str(db_path),
            "--codex-home",
            str(codex_home),
        ],
        cwd=workspace_root,
    )

    _ingest_claude(workspace_root, db_path)
    _ingest_codex(codex_home, workspace_root)
    _ingest_opencode(python_executable, workspace_root, db_path)

    agent_counts, session_count = _sqlite_counts(db_path)
    for agent in ("claude", "codex", "opencode"):
        if agent_counts.get(agent, 0) < 1:
            raise AssertionError(f"Expected at least one ingested event for {agent}, got {agent_counts}")

    queries = _query_surface(python_executable, workspace_root, db_path)
    if queries["survey"]["counts"]["events"] < 3:
        raise AssertionError(f"Expected at least three events in survey output: {queries['survey']}")
    if queries["chronicle"]["timeline_count"] < 1:
        raise AssertionError(f"Expected chronicle timeline entries: {queries['chronicle']}")
    if queries["synopsis"]["session_count"] < 3:
        raise AssertionError(f"Expected synopsis session coverage: {queries['synopsis']}")

    return {
        "workspace_root": str(workspace_root),
        "db_path": str(db_path),
        "agent_event_counts": agent_counts,
        "session_count": session_count,
        "queries": queries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test Anamnesis client setup and end-to-end ingestion/query wiring for Claude Code, Codex, and OpenCode."
    )
    parser.add_argument("--workspace-root", help="Optional workspace root to reuse. A temporary workspace is created when omitted.")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace on success.")
    args = parser.parse_args(argv)

    if args.workspace_root:
        summary = smoke_client_connections(
            workspace_root=Path(args.workspace_root).expanduser().resolve(),
            python_executable=args.python_executable,
        )
        print(json.dumps(summary, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="anamnesis-client-smoke-") as tmp:
        workspace_root = Path(tmp) / "workspace"
        summary = smoke_client_connections(
            workspace_root=workspace_root,
            python_executable=args.python_executable,
        )
        print(json.dumps(summary, indent=2))
        if args.keep_workspace:
            retained = workspace_root.parent.with_name(workspace_root.parent.name + "-retained")
            if retained.exists():
                raise RuntimeError(f"Retained workspace already exists: {retained}")
            workspace_root.parent.rename(retained)
            print(json.dumps({"retained_workspace_root": str(retained / 'workspace')}, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
