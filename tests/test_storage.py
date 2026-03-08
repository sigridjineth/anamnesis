from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UQA_CHECKOUT = REPO_ROOT / "uqa"
if str(UQA_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(UQA_CHECKOUT))

try:
    importlib.import_module("uqa.engine")
except Exception:
    UQA_AVAILABLE = False
else:
    UQA_AVAILABLE = True

from anamnesis.models import CanonicalEvent
from anamnesis.query import MemoryQueryService
from anamnesis.storage import RawMemoryStore


@unittest.skipUnless(UQA_AVAILABLE, "UQA runtime dependencies are required for sidecar integration tests")
class StorageTests(unittest.TestCase):
    def test_append_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            store = RawMemoryStore(db_path)
            store.append_events([
                CanonicalEvent(
                    id="e1",
                    agent="claude",
                    session_id="s1",
                    project_id="proj",
                    ts="2026-03-08T00:00:00Z",
                    kind="prompt",
                    role="user",
                    content="How did we create the install script?",
                    payload={"file_touches": [{"path": "install.sh", "operation": "create"}]},
                ),
                CanonicalEvent(
                    id="e2",
                    agent="claude",
                    session_id="s1",
                    project_id="proj",
                    ts="2026-03-08T00:01:00Z",
                    kind="assistant_message",
                    role="assistant",
                    content="We created install.sh after testing curl bootstrap flows.",
                ),
            ])
            service = MemoryQueryService(store, uqa_repo_root=UQA_CHECKOUT)
            orient = service.orient()
            self.assertEqual(orient["counts"]["events"], 2)
            hits = service.search("install")
            self.assertGreaterEqual(len(hits), 1)
            trace = service.trace_file("install.sh")
            self.assertEqual(len(trace["touches"]), 1)
            decision = service.trace_decision("install")
            self.assertEqual(len(decision["sessions"]), 1)
            sql = service.sql("SELECT COUNT(*) AS n FROM events")
            self.assertEqual(sql["rows"][0]["n"], 2)


if __name__ == "__main__":
    unittest.main()
