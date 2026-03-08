from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.config import Settings
from anamnesis.flex_projection import FlexCellProjector
from anamnesis.models import CanonicalEvent
from anamnesis.storage import RawMemoryStore
from anamnesis.uqa_sidecar import UQASidecar


REPO_ROOT = Path(__file__).resolve().parents[1]


class FlexProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.raw_db = self.root / "memory.db"
        self.sidecar_db = self.root / "memory.uqa.db"
        self.settings = Settings(
            workspace_root=self.root,
            raw_db_path=self.raw_db,
            uqa_sidecar_path=self.sidecar_db,
            uqa_repo_root=REPO_ROOT / "uqa",
            default_limit=10,
        )
        store = RawMemoryStore(self.raw_db)
        store.append_events(
            [
                CanonicalEvent(
                    id="evt-1",
                    agent="claude",
                    session_id="ses-1",
                    project_id="/repo/app",
                    ts="2026-03-01T10:00:00Z",
                    kind="prompt",
                    role="user",
                    content="Investigate worker history",
                    payload={},
                ),
                CanonicalEvent(
                    id="evt-2",
                    agent="claude",
                    session_id="ses-1",
                    project_id="/repo/app",
                    ts="2026-03-01T10:01:00Z",
                    kind="tool_result",
                    role="tool",
                    content="updated worker implementation",
                    tool_name="Edit",
                    target_path="src/worker.py",
                    payload={"file_touches": [{"path": "src/worker.py", "operation": "edit"}]},
                ),
                CanonicalEvent(
                    id="evt-3",
                    agent="codex",
                    session_id="ses-2",
                    project_id="/repo/app",
                    ts="2026-03-01T11:00:00Z",
                    kind="tool_call",
                    role="tool",
                    content="delegate review",
                    tool_name="delegate",
                    payload={"metadata": {"delegate": {"child_session_id": "ses-3", "label": "delegates_to"}}},
                ),
                CanonicalEvent(
                    id="evt-4",
                    agent="codex",
                    session_id="ses-3",
                    project_id="/repo/app",
                    ts="2026-03-01T11:02:00Z",
                    kind="assistant_message",
                    role="assistant",
                    content="Worker review complete.",
                    payload={},
                ),
            ]
        )
        UQASidecar(self.raw_db, self.sidecar_db, repo_root=self.settings.uqa_repo_root).rebuild()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_rebuild_creates_flex_cell_with_expected_tables_and_rows(self) -> None:
        projector = FlexCellProjector(self.settings, self.raw_db, self.sidecar_db)
        with (
            patch("anamnesis.getflex_runtime.GetFlexRuntime.encode_texts", side_effect=lambda texts, **_: [b"\x00" * (128 * 4) for _ in texts]),
            patch("anamnesis.getflex_runtime.GetFlexRuntime.register_and_install_assets"),
            patch("anamnesis.getflex_runtime.GetFlexRuntime.run_claude_code_enrichment", return_value={"failures": []}),
        ):
            result = projector.rebuild()

        self.assertEqual(result["backend"], "uqa->flex-projection")
        self.assertTrue(projector.cell_path.exists())

        db = sqlite3.connect(projector.cell_path)
        db.row_factory = sqlite3.Row
        try:
            chunk_count = db.execute("SELECT COUNT(*) FROM _raw_chunks").fetchone()[0]
            source_count = db.execute("SELECT COUNT(*) FROM _raw_sources").fetchone()[0]
            file_identity_count = db.execute("SELECT COUNT(*) FROM _edges_file_identity").fetchone()[0]
            delegation_count = db.execute("SELECT COUNT(*) FROM _edges_delegations").fetchone()[0]
            repo_identity_count = db.execute("SELECT COUNT(*) FROM _edges_repo_identity").fetchone()[0]
            content_identity_count = db.execute("SELECT COUNT(*) FROM _edges_content_identity").fetchone()[0]
            url_identity_count = db.execute("SELECT COUNT(*) FROM _edges_url_identity").fetchone()[0]
            view_meta = db.execute("SELECT value FROM _meta WHERE key='description'").fetchone()[0]
            source_row = dict(db.execute("SELECT * FROM _enrich_source_graph ORDER BY source_id LIMIT 1").fetchone())
        finally:
            db.close()

        self.assertEqual(chunk_count, 4)
        self.assertEqual(source_count, 3)
        self.assertGreaterEqual(file_identity_count, 1)
        self.assertGreaterEqual(delegation_count, 1)
        self.assertGreaterEqual(repo_identity_count, 1)
        self.assertGreaterEqual(content_identity_count, 1)
        self.assertGreaterEqual(url_identity_count, 0)
        self.assertIn("Anamnesis projected claude_code cell", view_meta)
        self.assertIn("community_id", source_row)
        self.assertIn("centrality", source_row)


if __name__ == "__main__":
    unittest.main()
