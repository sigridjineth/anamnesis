from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.cli import (
    PUBLIC_PRESET_TO_RUNTIME,
    execute_mcp_query_text,
    execute_query,
    execute_query_text,
    merge_params_into_query,
    parse_macro_query,
    sync_projected_cell,
    translate_query_text,
)
from anamnesis.models import CanonicalEvent
from anamnesis.storage import RawMemoryStore


class CliSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "claude_code.db"
        store = RawMemoryStore(self.db_path)
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
            ]
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_parse_macro_query_and_param_merge(self) -> None:
        preset, args, positional = parse_macro_query('@artifact path="src/worker.py" limit=5')
        self.assertEqual(preset, "@artifact")
        self.assertEqual(args["path"], "src/worker.py")
        self.assertEqual(args["limit"], "5")
        self.assertEqual(positional, [])
        self.assertEqual(
            merge_params_into_query("@chronicle", {"session": "ses-1", "limit": 3}),
            "@chronicle session=ses-1 limit=3",
        )

    def test_translate_query_text_maps_new_public_macros_to_runtime_macros(self) -> None:
        self.assertEqual(translate_query_text("@survey"), PUBLIC_PRESET_TO_RUNTIME["@survey"])
        translated = translate_query_text("!@artifact path=src/worker.py")
        self.assertTrue(translated.startswith(f'!{PUBLIC_PRESET_TO_RUNTIME["@artifact"]}'))
        with self.assertRaisesRegex(ValueError, "Legacy preset"):
            translate_query_text(f'{PUBLIC_PRESET_TO_RUNTIME["@chronicle"]} session=ses-1')
        with self.assertRaisesRegex(ValueError, "@thesis"):
            translate_query_text("@decision query=worker history")

    def test_execute_query_syncs_projection_and_parses_runtime_json(self) -> None:
        with (
            patch("anamnesis.cli.ProjectedCellProjector.ensure_ready") as ensure_ready,
            patch("anamnesis.preset_runtime.PresetRuntime.execute_cli_query", return_value='[{"session_id":"ses-1"}]') as execute_cli_query,
        ):
            result = execute_query("@chronicle session=ses-1", db_path=self.db_path, workspace_root=self.root)
        ensure_ready.assert_called_once()
        execute_cli_query.assert_called_once_with(
            cell_name="claude_code",
            query=f'{PUBLIC_PRESET_TO_RUNTIME["@chronicle"]} session=ses-1',
        )
        self.assertEqual(result[0]["session_id"], "ses-1")

    def test_execute_query_text_preserves_exact_runtime_output(self) -> None:
        text = '{"error":"Not valid SQL: \\\"worker history\\\""}'
        with (
            patch("anamnesis.cli.ProjectedCellProjector.ensure_ready"),
            patch("anamnesis.preset_runtime.PresetRuntime.execute_cli_query", return_value=text),
        ):
            result = execute_query_text("worker history", db_path=self.db_path, workspace_root=self.root)
        self.assertEqual(result, text)

    def test_execute_mcp_text_delegates_to_runtime(self) -> None:
        with (
            patch("anamnesis.cli.ProjectedCellProjector.ensure_ready"),
            patch("anamnesis.preset_runtime.PresetRuntime.execute_mcp_query", return_value="[1 rows, ~1 tok]\n[]") as execute_mcp_query,
        ):
            result = execute_mcp_query_text("@survey", db_path=self.db_path, workspace_root=self.root, params={"days": 7})
        execute_mcp_query.assert_called_once_with(
            cell_name="claude_code",
            query=PUBLIC_PRESET_TO_RUNTIME["@survey"],
            params={"days": 7},
        )
        self.assertEqual(result, "[1 rows, ~1 tok]\n[]")

    def test_execute_query_text_routes_thesis_to_memory_service_without_runtime(self) -> None:
        with (
            patch("anamnesis.cli.ProjectedCellProjector.ensure_ready") as ensure_ready,
            patch(
                "anamnesis.cli.MemoryService.trace_decision",
                return_value={"query": "worker history", "sessions": [], "results": []},
            ) as trace_decision,
        ):
            result = execute_query_text('@thesis query="worker history"', db_path=self.db_path, workspace_root=self.root)
        ensure_ready.assert_not_called()
        trace_decision.assert_called_once_with("worker history", db_path=str(self.db_path), limit=10, project_id=None)
        self.assertEqual(json.loads(result)["query"], "worker history")

    def test_sync_projected_cell_uses_projector(self) -> None:
        with patch("anamnesis.cli.ProjectedCellProjector.rebuild", return_value={"cell": "claude_code", "backend": "uqa->anamnesis-projection"}) as rebuild:
            result = sync_projected_cell(workspace_root=self.root, db_path=self.db_path)
        rebuild.assert_called_once()
        self.assertEqual(result["backend"], "uqa->anamnesis-projection")


if __name__ == "__main__":
    unittest.main()
