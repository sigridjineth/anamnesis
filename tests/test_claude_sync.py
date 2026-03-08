from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from anamnesis.claude_sync import ClaudeSyncService
from anamnesis.storage import RawMemoryStore


class ClaudeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.db_path = self.root / "memory.db"
        self.history_path = self.root / "history.jsonl"
        self.transcripts_root = self.root / "transcripts"
        self.transcripts_root.mkdir()
        self.projects_root = self.root / "projects"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_sync_imports_history_project_index_and_matching_transcripts(self) -> None:
        self.history_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "display": "inspect README",
                            "timestamp": 1759167412041,
                            "project": str(self.workspace),
                            "sessionId": "claude-session-1",
                        }
                    ),
                    json.dumps(
                        {
                            "display": "ignore another repo",
                            "timestamp": 1759167412042,
                            "project": str(self.root / "other"),
                            "sessionId": "claude-session-2",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        slug_dir = self.projects_root / str(self.workspace.resolve()).replace("/", "-")
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
                            "firstPrompt": "inspect README",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        (self.transcripts_root / "ses_match.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": "2026-03-08T00:02:00Z",
                            "content": "check file",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_use",
                            "timestamp": "2026-03-08T00:02:10Z",
                            "tool_name": "read",
                            "tool_input": {"filePath": str(self.workspace / "README.md")},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "tool_result",
                            "timestamp": "2026-03-08T00:02:11Z",
                            "tool_name": "read",
                            "tool_input": {"filePath": str(self.workspace / "README.md")},
                            "tool_output": {"preview": "hello"},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.transcripts_root / "ses_other.jsonl").write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "timestamp": "2026-03-08T00:03:00Z",
                    "tool_name": "read",
                    "tool_input": {"filePath": str(self.root / "other" / "README.md")},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        summary = ClaudeSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            transcripts_root=self.transcripts_root,
            projects_root=self.projects_root,
            workspace_root=self.workspace,
        )

        self.assertEqual(summary["history"]["payloads"], 1)
        self.assertEqual(summary["project_index"]["payloads"], 1)
        self.assertEqual(summary["transcripts"]["matched_files"], 1)

        db = sqlite3.connect(self.db_path)
        rows = db.execute(
            "SELECT agent, session_id, project_id, kind, tool_name, target_path, content FROM events ORDER BY ts, id"
        ).fetchall()
        touches = db.execute(
            "SELECT path, operation FROM file_touches ORDER BY path"
        ).fetchall()
        db.close()

        self.assertTrue(all(row[0] == "claude" for row in rows))
        self.assertTrue(all(row[2] == str(self.workspace.resolve()) for row in rows))
        self.assertIn(("claude-transcript:ses_match", str(self.workspace.resolve()), "tool_call"), {(row[1], row[2], row[3]) for row in rows})
        self.assertNotIn(("claude-transcript:ses_other",), {(row[1],) for row in rows})
        self.assertEqual(touches, [(str(self.workspace / "README.md"), "touch")])

    def test_sync_uses_project_session_ids_to_target_transcript_files(self) -> None:
        self.history_path.write_text(
            json.dumps(
                {
                    "display": "inspect README",
                    "timestamp": 1759167412041,
                    "project": str(self.workspace),
                    "sessionId": "ses_match",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        slug_dir = self.projects_root / str(self.workspace.resolve()).replace("/", "-")
        slug_dir.mkdir(parents=True)
        (slug_dir / "sessions-index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "ses_match",
                            "projectPath": str(self.workspace),
                            "created": "2026-03-08T00:00:00Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.transcripts_root / "ses_match.jsonl").write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "timestamp": "2026-03-08T00:02:10Z",
                    "tool_name": "read",
                    "tool_input": {"filePath": str(self.workspace / "README.md")},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.transcripts_root / "ses_other.jsonl").write_text("{not-json\n", encoding="utf-8")

        summary = ClaudeSyncService(RawMemoryStore(self.db_path)).sync(
            history_path=self.history_path,
            transcripts_root=self.transcripts_root,
            projects_root=self.projects_root,
            workspace_root=self.workspace,
        )

        self.assertEqual(summary["history"]["payloads"], 1)
        self.assertEqual(summary["project_index"]["payloads"], 1)
        self.assertEqual(summary["transcripts"]["matched_files"], 1)
        self.assertEqual(summary["transcripts"]["failures"], [])


if __name__ == "__main__":
    unittest.main()
