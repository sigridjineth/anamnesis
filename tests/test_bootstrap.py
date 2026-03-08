from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anamnesis.bootstrap import BootstrapConfig, BootstrapService


REPO_ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()
        self.db_path = self.workspace / ".anamnesis" / "anamnesis.db"

        self.claude_history = self.root / "claude-history.jsonl"
        self.claude_transcripts = self.root / "claude-transcripts"
        self.claude_projects = self.root / "claude-projects"
        self.codex_history = self.root / "codex-history.jsonl"
        self.codex_sessions = self.root / "codex-sessions"
        self.opencode_storage = self.root / "opencode-storage"

        self.claude_transcripts.mkdir()
        self.codex_sessions.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_bootstrap_initializes_workspace_and_imports_all_clients(self) -> None:
        self._write_claude_sources()
        self._write_codex_sources()
        self._write_opencode_storage()

        config = BootstrapConfig(
            workspace_root=self.workspace,
            python_executable="/usr/bin/python3",
            db_path=self.db_path,
            codex_home=self.codex_home,
            register_codex=False,
            claude_history_path=self.claude_history,
            claude_transcripts_root=self.claude_transcripts,
            claude_projects_root=self.claude_projects,
            codex_history_path=self.codex_history,
            codex_sessions_root=self.codex_sessions,
            opencode_storage_roots=(self.opencode_storage,),
            uqa_repo_root=REPO_ROOT / "uqa",
        )

        summary = BootstrapService(config).run()

        self.assertTrue((self.workspace / ".mcp.json").exists())
        self.assertTrue((self.workspace / ".claude" / "settings.local.json").exists())
        self.assertTrue((self.codex_home / "settings.json").exists())
        self.assertTrue((self.workspace / ".opencode" / "opencode.json").exists())
        self.assertTrue((self.db_path).exists())
        self.assertTrue((self.db_path.with_suffix(".uqa.db")).exists())
        self.assertEqual(set(summary["counts"]["agents"]), {"claude", "codex", "opencode"})
        self.assertGreaterEqual(summary["counts"]["events"], 6)

    def _write_claude_sources(self) -> None:
        self.claude_history.write_text(
            json.dumps(
                {
                    "display": "inspect workspace readme",
                    "timestamp": 1759167412041,
                    "project": str(self.workspace),
                    "sessionId": "claude-session-1",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        slug_dir = self.claude_projects / str(self.workspace.resolve()).replace("/", "-")
        slug_dir.mkdir(parents=True)
        (slug_dir / "sessions-index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "claude-session-1",
                            "projectPath": str(self.workspace),
                            "created": "2026-03-08T00:00:00Z",
                            "modified": "2026-03-08T00:01:00Z",
                            "gitBranch": "main",
                            "firstPrompt": "inspect workspace readme",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.claude_transcripts / "ses_claude.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "timestamp": "2026-03-08T00:02:00Z", "content": "trace file"}),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": "2026-03-08T00:02:01Z",
                            "tool_name": "read",
                            "tool_input": {"filePath": str(self.workspace / "README.md")},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_codex_sources(self) -> None:
        self.codex_history.write_text(
            json.dumps(
                {
                    "session_id": "codex-session-1",
                    "ts": 1754609319,
                    "text": f"check {self.workspace / 'src/app.py'}",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.codex_sessions / "session.json").write_text(
            json.dumps(
                {
                    "session": {"id": "codex-session-1", "timestamp": "2025-04-17T04:15:33.119Z"},
                    "items": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "shell",
                            "arguments": json.dumps({"file_path": str(self.workspace / "src/app.py"), "command": ["bash", "-lc", "pwd"]}),
                            "status": "completed",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call-1",
                            "output": json.dumps({"output": "pwd output"}),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _write_opencode_storage(self) -> None:
        session_id = "ses_1"
        project_id = str(self.workspace)
        session_dir = self.opencode_storage / "session" / Path(project_id.lstrip("/"))
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / f"{session_id}.json").write_text(
            json.dumps(
                {
                    "id": session_id,
                    "slug": "sample",
                    "version": "0.0.0-dev",
                    "projectID": project_id,
                    "directory": str(self.workspace),
                    "title": "Sample session",
                    "time": {"created": 1772764212176, "updated": 1772764212570},
                    "summary": {"additions": 0, "deletions": 0, "files": 0},
                }
            ),
            encoding="utf-8",
        )
        message_dir = self.opencode_storage / "message" / session_id
        message_dir.mkdir(parents=True, exist_ok=True)
        part_dir = self.opencode_storage / "part" / "msg_assistant"
        part_dir.mkdir(parents=True, exist_ok=True)
        (message_dir / "msg_assistant.json").write_text(
            json.dumps(
                {
                    "id": "msg_assistant",
                    "sessionID": session_id,
                    "role": "assistant",
                    "time": {"created": 1772764212233, "completed": 1772764212703},
                    "agent": "Default",
                    "providerID": "x",
                    "modelID": "y",
                }
            ),
            encoding="utf-8",
        )
        (part_dir / "tool1.json").write_text(
            json.dumps(
                {
                    "id": "tool1",
                    "sessionID": session_id,
                    "messageID": "msg_assistant",
                    "type": "tool",
                    "callID": "call_1",
                    "tool": "write",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": str(self.workspace / "src/app.py")},
                        "output": "updated",
                        "title": "Write file",
                        "metadata": {},
                        "time": {"start": 1772764212235, "end": 1772764212236},
                    },
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
