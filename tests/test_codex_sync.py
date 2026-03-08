from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from anamnesis.codex_sync import CodexSyncService
from anamnesis.storage import RawMemoryStore


class CodexSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "memory.db"
        self.history_path = self.root / "history.jsonl"
        self.sessions_root = self.root / "sessions"
        self.sessions_root.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_sync_imports_history_and_sessions(self) -> None:
        self.history_path.write_text(
            json.dumps(
                {
                    "session_id": "session-1",
                    "ts": 1754609319,
                    "text": "backfill this prompt",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        session_payload = {
            "session": {
                "id": "session-1",
                "timestamp": "2025-04-17T04:15:33.119Z",
            },
            "items": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "duplicate prompt in transcript"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "shell",
                    "arguments": json.dumps({"file_path": "src/app.py", "command": ["bash", "-lc", "pwd"]}),
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"output": "pwd output"}),
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "finished"}],
                },
            ],
        }
        (self.sessions_root / "sample.json").write_text(
            json.dumps(session_payload),
            encoding="utf-8",
        )

        summary = CodexSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            sessions_root=self.sessions_root,
            project_id="proj",
        )

        self.assertEqual(summary["history"]["payloads"], 1)
        self.assertEqual(summary["history"]["events"], 1)
        self.assertEqual(summary["sessions"]["payloads"], 3)
        self.assertEqual(summary["sessions"]["events"], 3)

        db = sqlite3.connect(self.db_path)
        rows = db.execute(
            "SELECT kind, tool_name, target_path, project_id, content FROM events ORDER BY id"
        ).fetchall()
        touches = db.execute(
            "SELECT path, operation FROM file_touches ORDER BY path"
        ).fetchall()
        db.close()

        self.assertEqual(sorted(row[0] for row in rows), ["assistant_message", "prompt", "tool_call", "tool_result"])
        tool_call = next(row for row in rows if row[0] == "tool_call")
        tool_result = next(row for row in rows if row[0] == "tool_result")
        self.assertEqual(tool_call[1], "shell")
        self.assertEqual(tool_call[2], "src/app.py")
        self.assertEqual(tool_call[3], "proj")
        self.assertEqual(tool_result[4], "pwd output")
        self.assertEqual(touches, [("src/app.py", "touch")])

    def test_sync_can_include_user_messages_from_sessions(self) -> None:
        (self.sessions_root / "sample.json").write_text(
            json.dumps(
                {
                    "session": {"id": "session-2", "timestamp": "2025-04-17T04:15:33.119Z"},
                    "items": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "user prompt"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        summary = CodexSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            sessions_root=self.sessions_root,
            include_history=False,
            include_user_messages=True,
        )

        self.assertEqual(summary["sessions"]["payloads"], 1)
        db = sqlite3.connect(self.db_path)
        row = db.execute("SELECT kind, content FROM events").fetchone()
        db.close()
        self.assertEqual(row, ("prompt", "user prompt"))

    def test_sync_filters_to_workspace_and_force_sets_canonical_project_id(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.history_path.write_text(
            "\n".join(
                [
                    json.dumps({"session_id": "match", "ts": 1754609319, "text": f"open {workspace / 'src/app.py'}"}),
                    json.dumps({"session_id": "other", "ts": 1754609320, "text": "ignore me"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.sessions_root / "match.json").write_text(
            json.dumps(
                {
                    "session": {"id": "match", "timestamp": "2025-04-17T04:15:33.119Z"},
                    "items": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "shell",
                            "arguments": json.dumps({"file_path": str(workspace / "src/app.py"), "command": ["bash", "-lc", "pwd"]}),
                            "status": "completed",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.sessions_root / "other.json").write_text(
            json.dumps(
                {
                    "session": {"id": "other", "timestamp": "2025-04-17T04:15:33.119Z"},
                    "items": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ignore"}]}],
                }
            ),
            encoding="utf-8",
        )

        summary = CodexSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            sessions_root=self.sessions_root,
            workspace_root=workspace,
            project_id=str(workspace.resolve()),
            force_project_id=True,
        )

        self.assertEqual(summary["history"]["payloads"], 1)
        self.assertEqual(summary["sessions"]["payloads"], 1)
        db = sqlite3.connect(self.db_path)
        projects = db.execute("SELECT DISTINCT project_id FROM events").fetchall()
        db.close()
        self.assertEqual(projects, [(str(workspace.resolve()),)])

    def test_sync_uses_history_matched_session_ids_to_avoid_irrelevant_full_scan(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.history_path.write_text(
            json.dumps({"session_id": "match", "ts": 1754609319, "text": f"open {workspace / 'src/app.py'}"}) + "\n",
            encoding="utf-8",
        )
        (self.sessions_root / "rollout-match.json").write_text(
            json.dumps(
                {
                    "session": {"id": "match", "timestamp": "2025-04-17T04:15:33.119Z"},
                    "items": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "shell",
                            "arguments": json.dumps({"file_path": str(workspace / "src/app.py")}),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.sessions_root / "rollout-other.json").write_text("{not-json", encoding="utf-8")

        summary = CodexSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            sessions_root=self.sessions_root,
            workspace_root=workspace,
            project_id=str(workspace.resolve()),
            force_project_id=True,
        )

        self.assertEqual(summary["history"]["payloads"], 1)
        self.assertEqual(summary["sessions"]["payloads"], 1)


if __name__ == "__main__":
    unittest.main()
