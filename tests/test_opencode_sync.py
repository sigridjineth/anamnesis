from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.opencode_sync import (
    OpenCodeSyncService,
    list_opencode_session_ids,
    parse_export_text,
)
from anamnesis.storage import RawMemoryStore


SAMPLE_EXPORT = {
    "info": {
        "id": "ses_1",
        "slug": "sample",
        "version": "0.0.0-dev",
        "projectID": "global",
        "directory": "/tmp/project",
        "title": "Sample session",
        "time": {"created": 1772764212176, "updated": 1772764212570},
        "summary": {"additions": 0, "deletions": 0, "files": 0},
    },
    "messages": [
        {
            "info": {
                "id": "msg_user",
                "sessionID": "ses_1",
                "role": "user",
                "time": {"created": 1772764212220},
                "summary": {"diffs": []},
                "agent": "Default",
                "model": {"providerID": "x", "modelID": "y"},
            },
            "parts": [
                {
                    "id": "prt_user",
                    "sessionID": "ses_1",
                    "messageID": "msg_user",
                    "type": "text",
                    "text": "hi",
                }
            ],
        },
        {
            "info": {
                "id": "msg_assistant",
                "sessionID": "ses_1",
                "role": "assistant",
                "time": {"created": 1772764212233, "completed": 1772764212703},
                "agent": "Default",
                "providerID": "x",
                "modelID": "y",
            },
            "parts": [
                {
                    "id": "prt_text",
                    "sessionID": "ses_1",
                    "messageID": "msg_assistant",
                    "type": "text",
                    "text": "hello from assistant",
                },
                {
                    "id": "prt_tool",
                    "sessionID": "ses_1",
                    "messageID": "msg_assistant",
                    "type": "tool",
                    "callID": "call_1",
                    "tool": "edit",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "src/app.py"},
                        "output": "updated file",
                        "title": "Edit file",
                        "metadata": {},
                        "time": {"start": 1772764212235, "end": 1772764212236},
                    },
                },
            ],
        },
    ],
}


class OpenCodeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "memory.db"
        self.export_path = self.root / "session.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_parse_export_text_strips_prefix(self) -> None:
        text = "Exporting session: ses_1" + json.dumps(SAMPLE_EXPORT)
        payload = parse_export_text(text)
        self.assertEqual(payload["info"]["id"], "ses_1")

    def test_list_session_ids_parses_table_output(self) -> None:
        output = (
            "Session ID  Title  Updated\n"
            "────────────────────\n"
            "ses_abc  Hello world  now\n"
            "ses_def  Another title  later\n"
        )
        with patch("subprocess.run") as run:
            run.return_value.stdout = output
            run.return_value.returncode = 0
            ids = list_opencode_session_ids()
        self.assertEqual(ids, ["ses_abc", "ses_def"])

    def test_sync_imports_export_file(self) -> None:
        self.export_path.write_text(json.dumps(SAMPLE_EXPORT), encoding="utf-8")
        summary = OpenCodeSyncService(RawMemoryStore(self.db_path)).sync(
            export_files=[self.export_path],
            project_id="proj",
        )
        self.assertEqual(summary["payloads"], 1)
        self.assertEqual(summary["failures"], [])

        db = sqlite3.connect(self.db_path)
        kinds = db.execute(
            "SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY kind"
        ).fetchall()
        file_touch = db.execute(
            "SELECT path FROM file_touches ORDER BY path"
        ).fetchall()
        db.close()

        self.assertEqual(
            kinds,
            [
                ("assistant_message", 1),
                ("prompt", 1),
                ("session_state", 1),
                ("tool_call", 1),
                ("tool_result", 1),
            ],
        )
        self.assertEqual(file_touch, [("src/app.py",)])

    def test_sync_records_parse_failures_and_continues(self) -> None:
        bad = self.root / "bad.json"
        good = self.root / "good.json"
        bad.write_text("not json", encoding="utf-8")
        good.write_text(json.dumps(SAMPLE_EXPORT), encoding="utf-8")

        summary = OpenCodeSyncService(RawMemoryStore(self.db_path)).sync(
            export_files=[bad, good],
        )
        self.assertEqual(summary["payloads"], 1)
        self.assertEqual(len(summary["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
