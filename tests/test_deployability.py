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

from agent_memory.mcp_server import create_server


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
EXPECTED_SCRIPTS = {
    "anamnesis-init": "agent_memory.init_cli:main",
    "anamnesis-mcp": "agent_memory.mcp_server:main",
    "anamnesis-ingest": "agent_memory.ingest:main",
    "anamnesis-codex-sync": "agent_memory.codex_sync:main",
    "anamnesis-opencode-sync": "agent_memory.opencode_sync:main",
    "anamnesis-hook-claude": "agent_memory.hooks.claude:main",
    "anamnesis-hook-codex": "agent_memory.hooks.codex:main",
    "anamnesis-hook-opencode": "agent_memory.hooks.opencode:main",
}


class DeployabilityTests(unittest.TestCase):
    def test_pyproject_declares_build_metadata_and_console_scripts(self) -> None:
        data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(data["project"]["name"], "anamnesis")
        self.assertEqual(data["project"]["requires-python"], ">=3.12")
        self.assertIn("mcp>=1.0.0", data["project"]["optional-dependencies"]["mcp"])

        scripts = data["project"]["scripts"]
        for name, target in EXPECTED_SCRIPTS.items():
            self.assertEqual(scripts[name], target)

        package_find = data["tool"]["setuptools"]["packages"]["find"]
        self.assertIn("agent_memory*", package_find["include"])
        self.assertIn("uqa*", package_find["exclude"])
        self.assertIn("flex*", package_find["exclude"])
        self.assertIn("tests*", package_find["exclude"])

    def test_console_script_targets_are_importable(self) -> None:
        for name, target in EXPECTED_SCRIPTS.items():
            module_name, attr_name = target.split(":", 1)
            module = import_module(module_name)
            entrypoint = getattr(module, attr_name)
            self.assertTrue(callable(entrypoint), msg=f"{name} -> {target} is not callable")

    def test_cli_modules_expose_help(self) -> None:
        commands = [
            (["-m", "agent_memory.init_cli", "--help"], "Write deployable Claude/Codex/OpenCode configuration for Anamnesis"),
            (["-m", "agent_memory.ingest", "--help"], "Normalize agent hook payloads"),
            (["-m", "agent_memory.codex_sync", "--help"], "Backfill Codex history"),
            (["-m", "agent_memory.opencode_sync", "--help"], "Backfill OpenCode exported sessions"),
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
                    "agent_memory.hooks.codex",
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
            row = db.execute(
                "SELECT agent, kind, tool_name, content FROM events"
            ).fetchone()
            db.close()

        self.assertEqual(row, ("codex", "tool_call", "shell", "bash -lc pwd"))

    def test_create_server_raises_clear_install_hint_when_mcp_is_missing(self) -> None:
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "mcp" or name.startswith("mcp."):
                raise ImportError("mcp unavailable")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaisesRegex(RuntimeError, r"pip install '\.\[mcp\]'"):
                create_server()


if __name__ == "__main__":
    unittest.main()
