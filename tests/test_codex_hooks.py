from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CodexHookRoutingTests(unittest.TestCase):
    def test_codex_hook_routes_to_workspace_local_dbs_and_canonicalizes_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_a = root / "repo-a"
            workspace_b = root / "repo-b"
            for workspace in (workspace_a, workspace_b):
                (workspace / ".git").mkdir(parents=True)

            payloads = "\n".join(
                [
                    json.dumps(
                        {
                            "tool": "UserPrompt",
                            "session": "session-a",
                            "cwd": str(workspace_a / "src"),
                            "ts": 1772932800,
                            "prompt": "hello a",
                        }
                    ),
                    json.dumps(
                        {
                            "tool": "UserPrompt",
                            "session": "session-b",
                            "cwd": str(workspace_b / "nested" / "pkg"),
                            "ts": 1772932801,
                            "prompt": "hello b",
                        }
                    ),
                ]
            )

            completed = subprocess.run(
                [sys.executable, "-m", "anamnesis.hooks.codex", "--quiet"],
                cwd=REPO_ROOT,
                input=payloads,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)

            db_a = workspace_a / ".anamnesis" / "anamnesis.db"
            db_b = workspace_b / ".anamnesis" / "anamnesis.db"
            self.assertTrue(db_a.exists())
            self.assertTrue(db_b.exists())

            with sqlite3.connect(db_a) as conn:
                rows_a = conn.execute("SELECT project_id, content FROM events").fetchall()
            with sqlite3.connect(db_b) as conn:
                rows_b = conn.execute("SELECT project_id, content FROM events").fetchall()

            self.assertEqual(rows_a, [(str(workspace_a.resolve()), "hello a")])
            self.assertEqual(rows_b, [(str(workspace_b.resolve()), "hello b")])


if __name__ == "__main__":
    unittest.main()
