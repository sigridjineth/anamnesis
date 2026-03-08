from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.config import Settings
from anamnesis.service import MemoryService


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.raw_db = self.root / "raw.db"
        self.sidecar_db = self.root / "raw.uqa.db"
        self.settings = Settings(
            workspace_root=self.root,
            raw_db_path=self.raw_db,
            uqa_sidecar_path=self.sidecar_db,
            uqa_repo_root=self.root / "uqa",
            default_limit=10,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_search_uses_uqa_backend(self) -> None:
        service = MemoryService(self.settings)
        with patch.object(MemoryService, "_query") as query_factory:
            query = query_factory.return_value
            query.search.return_value = [{"id": "e1", "score": 1.0}]
            result = service.search("install")
        self.assertEqual(result["backend"], "uqa")
        self.assertEqual(result["results"][0]["id"], "e1")
        query.search.assert_called_once()

    def test_sql_rejects_non_uqa_backend(self) -> None:
        service = MemoryService(self.settings)
        with self.assertRaisesRegex(ValueError, "UQA-only"):
            service.sql("SELECT 1", backend="flex")

    def test_ingest_rebuilds_uqa_sidecar(self) -> None:
        service = MemoryService(self.settings)
        events = []
        with (
            patch("anamnesis.service.RawMemoryStore.append_events", return_value=0) as append_events,
            patch.object(MemoryService, "rebuild_uqa_sidecar", return_value={"sidecar_path": str(self.sidecar_db)}) as rebuild,
        ):
            result = service.ingest(events)
        append_events.assert_called_once()
        rebuild.assert_called_once()
        self.assertIn("uqa_sidecar", result)


if __name__ == "__main__":
    unittest.main()
