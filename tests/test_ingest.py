from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory.ingest import IngestionService, load_payloads
from agent_memory.storage import RawMemoryStore


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "raw-memory.db"
        self.service = IngestionService(RawMemoryStore(self.db_path))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ingests_claude_prompt_payload(self) -> None:
        summary = self.service.ingest(
            "claude",
            [
                {
                    "event": "UserPromptSubmit",
                    "session_id": "s1",
                    "project": "proj",
                    "timestamp": "2026-03-08T00:00:00Z",
                    "prompt": "hello from claude",
                }
            ],
        )
        self.assertEqual(summary["events"], 1)
        db = sqlite3.connect(self.db_path)
        row = db.execute("SELECT agent, kind, content FROM events").fetchone()
        db.close()
        self.assertEqual(row, ("claude", "prompt", "hello from claude"))

    def test_ingests_file_touch_from_nested_tool_payload(self) -> None:
        self.service.ingest(
            "codex",
            [
                {
                    "type": "tool_call",
                    "session_id": "s2",
                    "cwd": "proj",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "src/app.py"},
                }
            ],
        )
        db = sqlite3.connect(self.db_path)
        file_touch = db.execute(
            "SELECT path, operation FROM file_touches"
        ).fetchone()
        db.close()
        self.assertEqual(file_touch, ("src/app.py", "edit"))

    def test_load_payloads_supports_jsonl(self) -> None:
        payloads = load_payloads(
            '{"event":"UserPromptSubmit","session_id":"s1"}\n'
            '{"event":"SessionEnd","session_id":"s1"}\n'
        )
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[1]["event"], "SessionEnd")


if __name__ == "__main__":
    unittest.main()
