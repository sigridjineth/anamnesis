from __future__ import annotations

import builtins
import json
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from anamnesis.mcp_server import create_server


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
EXPECTED_SCRIPTS = {
    "anamnesis": "anamnesis.cli:main",
    "anamnesis-init": "anamnesis.init_cli:main",
    "anamnesis-bootstrap": "anamnesis.bootstrap:main",
    "anamnesis-mcp": "anamnesis.mcp_server:main",
    "anamnesis-ingest": "anamnesis.ingest:main",
    "anamnesis-claude-sync": "anamnesis.claude_sync:main",
    "anamnesis-codex-sync": "anamnesis.codex_sync:main",
    "anamnesis-opencode-sync": "anamnesis.opencode_sync:main",
    "anamnesis-hook-claude": "anamnesis.hooks.claude:main",
    "anamnesis-hook-codex": "anamnesis.hooks.codex:main",
    "anamnesis-hook-opencode": "anamnesis.hooks.opencode:main",
}


class DeployabilityTests(unittest.TestCase):
    def test_pyproject_declares_uv_workspace_metadata_and_console_scripts(self) -> None:
        data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(data["build-system"]["build-backend"], "uv_build")
        self.assertEqual(data["project"]["name"], "anamnesis")
        self.assertEqual(data["project"]["requires-python"], ">=3.12")
        self.assertIn("uqa>=0.2.1", data["project"]["dependencies"])
        self.assertIn("mcp>=1.0.0", data["project"]["optional-dependencies"]["mcp"])
        self.assertEqual(data["tool"]["uv"]["workspace"]["members"], ["uqa"])
        self.assertEqual(data["tool"]["uv"]["sources"]["uqa"], {"workspace": True})
        self.assertEqual(data["tool"]["uv"]["build-backend"]["module-root"], "")

        scripts = data["project"]["scripts"]
        for name, target in EXPECTED_SCRIPTS.items():
            self.assertEqual(scripts[name], target)

    def test_console_script_targets_are_importable(self) -> None:
        for name, target in EXPECTED_SCRIPTS.items():
            module_name, attr_name = target.split(":", 1)
            module = import_module(module_name)
            entrypoint = getattr(module, attr_name)
            self.assertTrue(callable(entrypoint), msg=f"{name} -> {target} is not callable")

    def test_cli_modules_expose_help(self) -> None:
        commands = [
            (["-m", "anamnesis", "--help"], "Anamnesis — searchable shared memory for Claude, Codex, and OpenCode."),
            (["-m", "anamnesis.bootstrap", "--help"], "Initialize a workspace and backfill local Claude/Codex/OpenCode history"),
            (["-m", "anamnesis.init_cli", "--help"], "Write deployable Claude/Codex/OpenCode configuration for Anamnesis"),
            (["-m", "anamnesis.ingest", "--help"], "Normalize agent hook payloads"),
            (["-m", "anamnesis.claude_sync", "--help"], "Backfill Claude Code history"),
            (["-m", "anamnesis.codex_sync", "--help"], "Backfill Codex history"),
            (["-m", "anamnesis.opencode_sync", "--help"], "Backfill OpenCode exported sessions"),
        ]

        for args, expected in commands:
            completed = subprocess.run(
                [sys.executable, *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)
            self.assertIn(expected, completed.stdout)

    def test_codex_hook_wrapper_runs_without_explicit_agent_flag(self) -> None:
        payload = {
            "type": "function_call",
            "session_id": "session-1",
            "cwd": "proj",
            "name": "shell",
            "arguments": json.dumps({"command": ["bash", "-lc", "pwd"]}),
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "anamnesis.hooks.codex",
                    "--db",
                    str(db_path),
                    "--quiet",
                ],
                cwd=REPO_ROOT,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)

            db = sqlite3.connect(db_path)
            row = db.execute("SELECT agent, kind, tool_name, content FROM events").fetchone()
            db.close()

        self.assertEqual(row, ("codex", "tool_call", "shell", "bash -lc pwd"))

    def test_create_server_raises_clear_install_hint_when_mcp_is_missing(self) -> None:
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "mcp" or name.startswith("mcp."):
                raise ImportError("mcp unavailable")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, r"uv sync --extra mcp"):
                create_server()


if __name__ == "__main__":
    unittest.main()
