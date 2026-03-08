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
            service.sql("SELECT 1", backend="legacy")

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

    def test_trace_file_forwards_project_id(self) -> None:
        service = MemoryService(self.settings)
        with patch.object(MemoryService, "_query") as query_factory:
            query = query_factory.return_value
            query.trace_file.return_value = {"touches": []}
            result = service.trace_file("README.md", project_id="/repo/app")
        query.trace_file.assert_called_once_with("README.md", limit=20, project_id="/repo/app")
        self.assertEqual(result["results"], [])

    def test_story_and_digest_forward_project_id(self) -> None:
        service = MemoryService(self.settings)
        with patch.object(MemoryService, "_query") as query_factory:
            query = query_factory.return_value
            query.story.return_value = {"timeline": []}
            query.digest.return_value = {"sessions": [], "top_files": []}
            story = service.story(session_id="ses-1", project_id="/repo/app")
            digest = service.digest(project_id="/repo/app")
        query.story.assert_called_once_with(
            session_id="ses-1",
            query=None,
            limit=50,
            project_id="/repo/app",
        )
        query.digest.assert_called_once_with(days=7, project_id="/repo/app")
        self.assertEqual(story["results"], [])
        self.assertEqual(digest["sessions"], [])

    def test_file_search_wraps_results_consistently(self) -> None:
        service = MemoryService(self.settings)
        with patch.object(MemoryService, "_query") as query_factory:
            query = query_factory.return_value
            query.file_search.return_value = [{"id": "f1", "path": "src/app.py", "score": 0.9}]
            result = service.file_search("app")
        self.assertEqual(result["backend"], "uqa")
        self.assertEqual(result["results"][0]["id"], "f1")
        self.assertEqual(result["files"][0]["path"], "src/app.py")

    def test_delegation_tree_flattens_nested_steps_into_results(self) -> None:
        service = MemoryService(self.settings)
        with patch.object(MemoryService, "_query") as query_factory:
            query = query_factory.return_value
            query.delegation_tree.return_value = {
                "sessions": [
                    {"session_id": "s1", "steps": [{"base_event_id": "evt-1"}]},
                    {"session_id": "s2", "steps": [{"base_event_id": "evt-2"}]},
                ]
            }
            result = service.delegation_tree(session_id="s1")
        self.assertEqual([row["base_event_id"] for row in result["results"]], ["evt-1", "evt-2"])

    def test_new_public_alias_methods_delegate_to_existing_service_methods(self) -> None:
        service = MemoryService(self.settings)
        with (
            patch.object(service, "orient", return_value={"backend": "uqa"}) as orient,
            patch.object(service, "digest", return_value={"sessions": []}) as digest,
            patch.object(service, "trace_file", return_value={"touches": []}) as trace_file,
            patch.object(service, "story", return_value={"timeline": []}) as story,
            patch.object(service, "sprints", return_value={"sprints": []}) as sprints,
            patch.object(service, "genealogy", return_value={"timeline": []}) as genealogy,
            patch.object(service, "bridges", return_value={"bridges": []}) as bridges,
            patch.object(service, "delegation_tree", return_value={"sessions": []}) as delegation_tree,
            patch.object(service, "trace_decision", return_value={"sessions": []}) as trace_decision,
            patch.object(service, "health", return_value={"mode": "uqa-mandatory"}) as health,
        ):
            self.assertEqual(service.survey(project_id="/repo/app")["backend"], "uqa")
            self.assertEqual(service.synopsis(days=3)["sessions"], [])
            self.assertEqual(service.artifact("README.md")["touches"], [])
            self.assertEqual(service.chronicle(session_id="ses-1")["timeline"], [])
            self.assertEqual(service.cadence(days=5)["sprints"], [])
            self.assertEqual(service.lineage("install")["timeline"], [])
            self.assertEqual(service.crossroads("install")["bridges"], [])
            self.assertEqual(service.relay(session_id="ses-1")["sessions"], [])
            self.assertEqual(service.thesis("install")["sessions"], [])
            self.assertEqual(service.vitals()["mode"], "uqa-mandatory")

        orient.assert_called_once_with(db_path=None, project_id="/repo/app")
        digest.assert_called_once_with(days=3, db_path=None, project_id=None)
        trace_file.assert_called_once_with("README.md", db_path=None, limit=20, project_id=None)
        story.assert_called_once_with(session_id="ses-1", query=None, db_path=None, limit=50, project_id=None)
        sprints.assert_called_once_with(days=5, db_path=None, project_id=None, gap_hours=4)
        genealogy.assert_called_once_with("install", db_path=None, limit=20, project_id=None)
        bridges.assert_called_once_with("install", None, db_path=None, limit=10, project_id=None)
        delegation_tree.assert_called_once_with(session_id="ses-1", query=None, db_path=None, limit=50, project_id=None)
        trace_decision.assert_called_once_with("install", db_path=None, limit=10, project_id=None)
        health.assert_called_once_with(db_path=None)


if __name__ == "__main__":
    unittest.main()
