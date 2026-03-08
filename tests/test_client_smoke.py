from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_client_connections.py"


class ClientSmokeTests(unittest.TestCase):
    def test_smoke_client_connections_script(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        summary = json.loads(completed.stdout)
        self.assertEqual(set(summary["agent_event_counts"]), {"claude", "codex", "opencode"})
        self.assertGreaterEqual(summary["queries"]["survey"]["counts"]["events"], 3)
        self.assertGreaterEqual(summary["queries"]["chronicle"]["timeline_count"], 1)
        self.assertGreaterEqual(summary["queries"]["synopsis"]["session_count"], 3)


if __name__ == "__main__":
    unittest.main()
