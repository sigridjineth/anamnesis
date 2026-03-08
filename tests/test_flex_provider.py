from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory.providers.flex import FlexCellProvider, guard_read_only


class FlexProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "cell.db"
        db = sqlite3.connect(self.db_path)
        db.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, project TEXT, created_at TEXT, type TEXT, tool_name TEXT, path TEXT, content TEXT)"
        )
        db.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("1", "s1", "alpha", "2026-03-01", "user_prompt", None, "src/a.py", "add search layer"),
                ("2", "s1", "alpha", "2026-03-01", "tool_result", "Edit", "src/a.py", "edited file history code"),
            ],
        )
        db.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project TEXT)")
        db.execute("INSERT INTO sessions VALUES ('s1', 'alpha')")
        db.commit()
        db.close()
        self.provider = FlexCellProvider(cell_path=self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_guard_read_only_blocks_mutations(self) -> None:
        with self.assertRaises(ValueError):
            guard_read_only("DELETE FROM messages")

    def test_orient_and_search(self) -> None:
        summary = self.provider.orient().to_dict()
        self.assertEqual(summary["cell_path"], str(self.db_path))
        relation_names = {relation["name"] for relation in summary["relations"]}
        self.assertIn("messages", relation_names)
        hits = self.provider.search_like("search", limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, "1")

    def test_trace_file(self) -> None:
        hits = self.provider.trace_file("src/a.py")
        self.assertEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
