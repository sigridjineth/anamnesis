from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from anamnesis.models import CanonicalEvent
from anamnesis.storage import RawMemoryStore
from anamnesis.uqa_sidecar import UQASidecar


REPO_ROOT = Path(__file__).resolve().parents[1]


class UQASidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.raw_db = self.root / "memory.db"
        self.sidecar_db = self.root / "memory.uqa.db"
        self.store = RawMemoryStore(self.raw_db)
        self.sidecar = UQASidecar(self.raw_db, self.sidecar_db, repo_root=REPO_ROOT / "uqa")

        events = [
            CanonicalEvent(
                id="evt-1",
                agent="claude",
                session_id="ses-1",
                project_id="/repo/app",
                ts="2026-03-01T10:00:00Z",
                kind="prompt",
                role="user",
                content="Investigate the curl install script and fix it",
                payload={},
            ),
            CanonicalEvent(
                id="evt-2:call",
                agent="claude",
                session_id="ses-1",
                project_id="/repo/app",
                ts="2026-03-01T10:01:00Z",
                kind="tool_call",
                role="tool",
                content="write scripts/install.sh",
                tool_name="write",
                target_path="scripts/install.sh",
                payload={
                    "file_touches": [
                        {"path": "scripts/install.sh", "operation": "edit"},
                        {"path": "README.md", "operation": "edit"},
                    ]
                },
            ),
            CanonicalEvent(
                id="evt-2:result",
                agent="claude",
                session_id="ses-1",
                project_id="/repo/app",
                ts="2026-03-01T10:01:30Z",
                kind="tool_result",
                role="tool",
                content="updated install script",
                tool_name="write",
                payload={},
            ),
            CanonicalEvent(
                id="evt-3",
                agent="claude",
                session_id="ses-1",
                project_id="/repo/app",
                ts="2026-03-01T10:02:00Z",
                kind="assistant_message",
                role="assistant",
                content="The curl install script now checks architecture before download.",
                payload={},
            ),
            CanonicalEvent(
                id="evt-4",
                agent="codex",
                session_id="ses-2",
                project_id="/repo/app",
                ts="2026-03-02T11:00:00Z",
                kind="prompt",
                role="user",
                content="Review deployment docs for install flow",
                payload={},
            ),
            CanonicalEvent(
                id="evt-5",
                agent="codex",
                session_id="ses-2",
                project_id="/repo/app",
                ts="2026-03-02T11:01:00Z",
                kind="tool_result",
                role="tool",
                content="updated deployment documentation",
                tool_name="edit",
                target_path="docs/deploy.md",
                payload={"file_touches": [{"path": "docs/deploy.md", "operation": "edit"}]},
            ),
        ]
        self.store.append_events(events)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_rebuild_materializes_rich_tables_graph_and_vectors(self) -> None:
        result = self.sidecar.rebuild()
        self.assertEqual(result["projects"], 1)
        self.assertEqual(result["sessions"], 2)
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["events"], 6)
        self.assertGreaterEqual(result["touch_activity"], 3)
        self.assertGreaterEqual(result["search_docs"], 9)
        self.assertGreater(result["graph_edges"], 0)

        orient = self.sidecar.orient(project_id="/repo/app")
        self.assertEqual(orient["counts"]["files"], 3)
        self.assertGreater(orient["vectors"], 0)
        self.assertGreater(orient["graph"]["vertices"], 0)
        self.assertNotIn("presets", orient)
        self.assertIn("chronicle", orient["macros"])
        self.assertIn("lineage", orient["macros"])

    def test_orient_and_health_return_raw_pending_snapshot_before_sidecar_is_ready(self) -> None:
        orient = self.sidecar.orient(project_id="/repo/app")
        self.assertEqual(orient["counts"]["events"], 6)
        self.assertEqual(orient["counts"]["sessions"], 2)
        self.assertEqual(orient["counts"]["files"], 3)
        self.assertTrue(orient["uqa"]["stale"])
        self.assertTrue(orient["uqa"]["rebuild_required"])
        self.assertFalse(orient["uqa"]["rebuild_in_progress"])
        self.assertIn("events", orient["tables"])

        health = self.sidecar.health()
        self.assertEqual(health["raw"]["events"], 6)
        self.assertTrue(health["status"]["stale"])
        self.assertTrue(health["status"]["rebuild_required"])

    def test_search_trace_file_trace_decision_and_digest_use_sidecar(self) -> None:
        self.sidecar.rebuild()

        hits = self.sidecar.search("curl install script", limit=5, project_id="/repo/app")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project_id"], "/repo/app")
        self.assertIn(hits[0]["entity_type"], {"project", "session", "event", "file"})

        file_trace = self.sidecar.trace_file("scripts/install.sh", limit=10)
        self.assertEqual(file_trace["canonical_path"], "scripts/install.sh")
        self.assertTrue(file_trace["touches"])
        self.assertEqual(file_trace["touches"][0]["path"], "scripts/install.sh")
        self.assertTrue(any(row["path"] == "scripts/install.sh" for row in file_trace["files"]))

        decision = self.sidecar.trace_decision("curl install script", limit=5)
        self.assertTrue(decision["sessions"])
        self.assertEqual(decision["sessions"][0]["session_id"], "ses-1")
        self.assertGreaterEqual(decision["sessions"][0]["event_count"], 4)

        digest = self.sidecar.digest(days=5000)
        self.assertEqual(len(digest["sessions"]), 2)
        self.assertTrue(any(row["path"] == "scripts/install.sh" for row in digest["top_files"]))

        story = self.sidecar.story(session_id="ses-1", limit=10)
        self.assertEqual(story["session"]["session_id"], "ses-1")
        self.assertTrue(story["timeline"])

        genealogy = self.sidecar.genealogy("install", limit=10)
        self.assertTrue(genealogy["timeline"])

        bridges = self.sidecar.bridges("install script", "deployment", limit=10)
        self.assertIn("shared_sessions", bridges)

        delegation = self.sidecar.delegation_tree(session_id="ses-1", limit=10)
        self.assertEqual(delegation["sessions"][0]["session_id"], "ses-1")
        self.assertTrue(delegation["sessions"][0]["steps"])

    def test_common_queries_fall_back_to_raw_store_when_sidecar_is_stale(self) -> None:
        hits = self.sidecar.search("curl install script", limit=5, project_id="/repo/app")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project_id"], "/repo/app")

        digest = self.sidecar.digest(days=5000, project_id="/repo/app")
        self.assertEqual(len(digest["sessions"]), 2)
        self.assertTrue(any(row["path"] == "scripts/install.sh" for row in digest["top_files"]))
        self.assertEqual(digest["uqa"]["mode"], "raw-fallback")

        decision = self.sidecar.trace_decision("curl install script", limit=5, project_id="/repo/app")
        self.assertTrue(decision["sessions"])
        self.assertEqual(decision["sessions"][0]["session_id"], "ses-1")
        self.assertGreaterEqual(decision["sessions"][0]["event_count"], 4)

        story = self.sidecar.story(session_id="ses-1", limit=10, project_id="/repo/app")
        self.assertEqual(story["session"]["session_id"], "ses-1")
        self.assertTrue(story["timeline"])

        story_by_query = self.sidecar.story(query="curl install script", limit=10, project_id="/repo/app")
        self.assertEqual(story_by_query["session"]["session_id"], "ses-1")

        file_trace = self.sidecar.trace_file("scripts/install.sh", limit=10, project_id="/repo/app")
        self.assertEqual(file_trace["canonical_path"], "scripts/install.sh")
        self.assertTrue(file_trace["touches"])
        self.assertTrue(any(row["path"] == "scripts/install.sh" for row in file_trace["files"]))

        delegation = self.sidecar.delegation_tree(session_id="ses-1", limit=10, project_id="/repo/app")
        self.assertTrue(delegation["sessions"])
        self.assertEqual(delegation["uqa"]["mode"], "raw-fallback")

    def test_stale_lock_file_is_cleared_before_reporting_or_rebuilding(self) -> None:
        lock_path = self.sidecar._rebuild_lock_path()  # noqa: SLF001 - regression coverage
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("999999999", encoding="utf-8")

        digest = self.sidecar.digest(days=5000, project_id="/repo/app")
        self.assertFalse(lock_path.exists())
        self.assertFalse(digest["uqa"]["rebuild_in_progress"])

        lock_path.write_text("999999999", encoding="utf-8")
        with self.sidecar._rebuild_lock(timeout_seconds=0.01):  # noqa: SLF001 - regression coverage
            self.assertTrue(lock_path.exists())
        self.assertFalse(lock_path.exists())

    def test_project_scoped_queries_keep_same_paths_separate_across_repos(self) -> None:
        self.store.append_events(
            [
                CanonicalEvent(
                    id="evt-ops-1",
                    agent="codex",
                    session_id="ops-ses",
                    project_id="/repo/ops",
                    ts="2026-03-03T09:00:00Z",
                    kind="prompt",
                    role="user",
                    content="Update the README incident rotation notes",
                    payload={},
                ),
                CanonicalEvent(
                    id="evt-ops-2",
                    agent="codex",
                    session_id="ops-ses",
                    project_id="/repo/ops",
                    ts="2026-03-03T09:01:00Z",
                    kind="tool_result",
                    role="tool",
                    content="updated README incident rotation notes",
                    tool_name="edit",
                    target_path="README.md",
                    payload={"file_touches": [{"path": "README.md", "operation": "edit"}]},
                ),
            ]
        )
        self.sidecar.rebuild()

        trace = self.sidecar.trace_file("README.md", limit=10, project_id="/repo/ops")
        self.assertEqual(trace["project_id"], "/repo/ops")
        self.assertEqual({row["project_id"] for row in trace["files"]}, {"/repo/ops"})
        self.assertEqual({row["project_id"] for row in trace["touches"]}, {"/repo/ops"})

        decision = self.sidecar.trace_decision("incident rotation", limit=5, project_id="/repo/ops")
        self.assertEqual({row["project_id"] for row in decision["sessions"]}, {"/repo/ops"})

        digest = self.sidecar.digest(days=5000, project_id="/repo/ops")
        self.assertEqual({row["project_id"] for row in digest["sessions"]}, {"/repo/ops"})
        self.assertEqual({row["project_id"] for row in digest["top_files"]}, {"/repo/ops"})

        story = self.sidecar.story(session_id="ops-ses", limit=10, project_id="/repo/ops")
        self.assertEqual(story["project_id"], "/repo/ops")
        self.assertTrue(all(row["project_id"] == "/repo/ops" for row in story["timeline"]))

    def test_trace_file_includes_copy_lineage_for_target_alias(self) -> None:
        self.store.append_events(
            [
                CanonicalEvent(
                    id="evt-copy",
                    agent="codex",
                    session_id="ses-2",
                    project_id="/repo/app",
                    ts="2026-03-02T11:02:00Z",
                    kind="tool_call",
                    role="tool",
                    content="cp scripts/install.sh scripts/install-copy.sh",
                    tool_name="shell",
                    payload={
                        "tool_input": {
                            "command": [
                                "cp",
                                "scripts/install.sh",
                                "scripts/install-copy.sh",
                            ]
                        }
                    },
                )
            ]
        )

        raw_trace = self.sidecar.trace_file("scripts/install-copy.sh", limit=10)
        self.assertTrue(raw_trace["lineage"])
        self.assertEqual(raw_trace["lineage"][0]["relation"], "copy")

        self.sidecar.rebuild()
        file_trace = self.sidecar.trace_file("scripts/install-copy.sh", limit=10)

        self.assertTrue(file_trace["files"])
        self.assertTrue(file_trace["lineage"])
        self.assertEqual(file_trace["lineage"][0]["relation"], "copy")
        self.assertEqual(file_trace["lineage"][0]["match_role"], "target")
        self.assertEqual(file_trace["lineage"][0]["counterpart_path"], "scripts/install.sh")

    def test_is_stale_detects_sidecar_count_mismatch_even_if_sidecar_is_newer(self) -> None:
        self.sidecar.rebuild()
        with sqlite3.connect(self.sidecar_db) as db:
            db.execute("DELETE FROM _data_events")
            db.execute("DELETE FROM _data_sessions")
            db.execute("DELETE FROM _data_touch_activity")
            db.commit()
        future_mtime = self.raw_db.stat().st_mtime + 60
        os.utime(self.sidecar_db, (future_mtime, future_mtime))
        self.assertTrue(self.sidecar._is_stale())  # noqa: SLF001 - explicit regression check

    def test_delegation_tree_walks_nested_session_links(self) -> None:
        self.store.append_events(
            [
                CanonicalEvent(
                    id="evt-delegate-root",
                    agent="codex",
                    session_id="ses-1",
                    project_id="/repo/app",
                    ts="2026-03-01T10:03:00Z",
                    kind="tool_call",
                    role="tool",
                    content="delegate review session",
                    tool_name="delegate",
                    payload={
                        "metadata": {
                            "delegate": {
                                "child_session_id": "ses-3",
                                "label": "delegates_to",
                            }
                        }
                    },
                ),
                CanonicalEvent(
                    id="evt-child",
                    agent="codex",
                    session_id="ses-3",
                    project_id="/repo/app",
                    ts="2026-03-01T10:04:00Z",
                    kind="prompt",
                    role="user",
                    content="Investigate the delegated review",
                    payload={},
                ),
                CanonicalEvent(
                    id="evt-delegate-child",
                    agent="codex",
                    session_id="ses-3",
                    project_id="/repo/app",
                    ts="2026-03-01T10:05:00Z",
                    kind="tool_call",
                    role="tool",
                    content="delegate nested session",
                    tool_name="delegate",
                    payload={
                        "metadata": {
                            "nested_delegate": {
                                "subagent_session_id": "ses-4",
                                "label": "delegates_to",
                            }
                        }
                    },
                ),
                CanonicalEvent(
                    id="evt-grandchild",
                    agent="codex",
                    session_id="ses-4",
                    project_id="/repo/app",
                    ts="2026-03-01T10:06:00Z",
                    kind="prompt",
                    role="user",
                    content="Investigate the nested delegated review",
                    payload={},
                ),
            ]
        )

        self.sidecar.rebuild()
        delegation = self.sidecar.delegation_tree(session_id="ses-1", limit=10)

        session_ids = [row["session_id"] for row in delegation["sessions"]]
        self.assertEqual(session_ids[0], "ses-1")
        self.assertIn("ses-3", session_ids)
        self.assertIn("ses-4", session_ids)

        root = next(row for row in delegation["sessions"] if row["session_id"] == "ses-1")
        child = next(row for row in delegation["sessions"] if row["session_id"] == "ses-3")
        grandchild = next(row for row in delegation["sessions"] if row["session_id"] == "ses-4")

        self.assertEqual(root["relation"], "root")
        self.assertTrue(any(step_child["label"] == "ses-3" for step in root["steps"] for step_child in step["children"]))
        self.assertEqual(child["relation"], "descendant")
        self.assertEqual(child["depth"], 1)
        self.assertTrue(any(link["child_session_id"] == "ses-4" for link in child["children"]))
        self.assertEqual(grandchild["depth"], 2)

    def test_project_filters_keep_results_repo_scoped(self) -> None:
        self.store.append_events(
            [
                CanonicalEvent(
                    id="evt-6",
                    agent="claude",
                    session_id="ses-3",
                    project_id="/repo/other",
                    ts="2026-03-03T12:00:00Z",
                    kind="prompt",
                    role="user",
                    content="Investigate unrelated deployment issue",
                    payload={},
                )
            ]
        )
        self.sidecar.rebuild()

        hits = self.sidecar.search("deployment", limit=10, project_id="/repo/other")
        self.assertTrue(hits)
        self.assertTrue(all(row["project_id"] == "/repo/other" for row in hits))

        sprints = self.sidecar.sprints(days=5000, project_id="/repo/other")
        self.assertTrue(sprints["sprints"])
        self.assertTrue(all(row["project_id"] == "/repo/other" for row in sprints["sprints"]))


if __name__ == "__main__":
    unittest.main()
