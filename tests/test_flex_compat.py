from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.flex_compat import (
    execute_flex_mcp_text,
    execute_flex_query,
    execute_flex_query_text,
    merge_params_into_query,
    parse_preset_query,
    sync_flex_cell,
)
from anamnesis.models import CanonicalEvent
from anamnesis.storage import RawMemoryStore


class FlexCompatTests(unittest.TestCase):
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

    def test_parse_preset_query_and_param_merge(self) -> None:
        preset, args, positional = parse_preset_query('@file path="src/worker.py" limit=5')
        self.assertEqual(preset, "@file")
        self.assertEqual(args["path"], "src/worker.py")
        self.assertEqual(args["limit"], "5")
        self.assertEqual(positional, [])
        self.assertEqual(
            merge_params_into_query("@story", {"session": "ses-1", "limit": 3}),
            "@story session=ses-1 limit=3",
        )

    def test_execute_query_syncs_projection_and_parses_runtime_json(self) -> None:
        with (
            patch("anamnesis.flex_compat.FlexCellProjector.ensure_ready") as ensure_ready,
            patch("anamnesis.getflex_runtime.GetFlexRuntime.execute_cli_query", return_value='[{"session_id":"ses-1"}]') as execute_cli_query,
        ):
            result = execute_flex_query("@story session=ses-1", db_path=self.db_path, workspace_root=self.root)
        ensure_ready.assert_called_once()
        execute_cli_query.assert_called_once()
        self.assertEqual(result[0]["session_id"], "ses-1")

    def test_execute_query_text_preserves_exact_runtime_output(self) -> None:
        text = '{"error":"Not valid SQL: \\\"worker history\\\""}'
        with (
            patch("anamnesis.flex_compat.FlexCellProjector.ensure_ready"),
            patch("anamnesis.getflex_runtime.GetFlexRuntime.execute_cli_query", return_value=text),
        ):
            result = execute_flex_query_text("worker history", db_path=self.db_path, workspace_root=self.root)
        self.assertEqual(result, text)

    def test_execute_mcp_text_delegates_to_runtime(self) -> None:
        with (
            patch("anamnesis.flex_compat.FlexCellProjector.ensure_ready"),
            patch("anamnesis.getflex_runtime.GetFlexRuntime.execute_mcp_query", return_value="[1 rows, ~1 tok]\n[]") as execute_mcp_query,
        ):
            result = execute_flex_mcp_text("@orient", db_path=self.db_path, workspace_root=self.root, params={"days": 7})
        execute_mcp_query.assert_called_once_with(cell_name="claude_code", query="@orient", params={"days": 7})
        self.assertEqual(result, "[1 rows, ~1 tok]\n[]")

    def test_sync_flex_cell_uses_projector(self) -> None:
        with patch("anamnesis.flex_compat.FlexCellProjector.rebuild", return_value={"cell": "claude_code", "backend": "uqa->flex-projection"}) as rebuild:
            result = sync_flex_cell(workspace_root=self.root, db_path=self.db_path)
        rebuild.assert_called_once()
        self.assertEqual(result["backend"], "uqa->flex-projection")

if __name__ == "__main__":
    unittest.main()
