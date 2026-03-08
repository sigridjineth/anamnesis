from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory.config import Settings
from agent_memory.service import MemoryService


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "cell.db"
        db = sqlite3.connect(self.db_path)
        db.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, project TEXT, created_at TEXT, type TEXT, tool_name TEXT, path TEXT, content TEXT)"
        )
        db.execute(
            "INSERT INTO messages VALUES ('1', 's1', 'alpha', '2026-03-01', 'user_prompt', NULL, 'src/a.py', 'search memory layer')"
        )
        db.commit()
        db.close()
        self.settings = Settings(
            workspace_root=Path(self.tempdir.name),
            flex_repo_root=None,
            uqa_repo_root=Path(self.tempdir.name) / "missing-uqa",
            flex_cell="claude_code",
            flex_cell_path=self.db_path,
            uqa_sidecar_path=Path(self.tempdir.name) / "sidecar.db",
            default_limit=10,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_search_falls_back_without_uqa(self) -> None:
        service = MemoryService(self.settings)
        result = service.search("search")
        self.assertEqual(result["backend"], "flex-like")
        self.assertEqual(len(result["results"]), 1)

    def test_sql_uses_flex_backend(self) -> None:
        service = MemoryService(self.settings)
        result = service.sql("SELECT id, project FROM messages", backend="flex")
        self.assertEqual(result["rows"][0]["id"], "1")

    def test_orient_uses_canonical_store_when_raw_db_is_default(self) -> None:
        settings = Settings(
            workspace_root=Path(self.tempdir.name),
            flex_repo_root=None,
            uqa_repo_root=Path(self.tempdir.name) / "missing-uqa",
            flex_cell="claude_code",
            flex_cell_path=None,
            uqa_sidecar_path=Path(self.tempdir.name) / "sidecar.db",
            default_limit=10,
            raw_db_path=Path(self.tempdir.name) / "raw-memory.db",
        )
        service = MemoryService(settings)
        result = service.orient()
        self.assertEqual(result["counts"]["events"], 0)
        self.assertIn("sessions", result["tables"])


if __name__ == "__main__":
    unittest.main()
