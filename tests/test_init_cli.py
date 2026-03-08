from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory.init_cli import InitConfig, InitService


class InitCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "flex").mkdir()
        (self.root / "uqa").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_init_writes_client_files(self) -> None:
        codex_home = self.root / ".fake-codex"
        config = InitConfig(
            workspace_root=self.root,
            python_executable="/usr/bin/python3",
            db_path=self.root / ".anamnesis" / "anamnesis.db",
            codex_home=codex_home,
            flex_repo_root=self.root / "flex",
            uqa_repo_root=self.root / "uqa",
        )

        summary = InitService(config).run()

        self.assertEqual(summary["clients"], ["claude", "codex", "opencode"])
        self.assertTrue((self.root / ".mcp.json").exists())
        self.assertTrue((self.root / ".claude" / "settings.local.json").exists())
        self.assertTrue((codex_home / "settings.json").exists())
        self.assertTrue((self.root / ".anamnesis" / "generated" / "register-codex-mcp.sh").exists())
        self.assertTrue((self.root / ".opencode" / "opencode.json").exists())
        self.assertTrue((self.root / ".opencode" / "plugins" / "anamnesis.ts").exists())

        mcp = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["anamnesis"]["command"], "/usr/bin/python3")
        self.assertEqual(
            mcp["mcpServers"]["anamnesis"]["env"]["ANAMNESIS_DB"],
            str(self.root / ".anamnesis" / "anamnesis.db"),
        )

        codex_settings = json.loads((codex_home / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("UserPromptSubmit", codex_settings["hooks"])
        self.assertIn("PostToolUse", codex_settings["hooks"])

        opencode_plugin = (self.root / ".opencode" / "plugins" / "anamnesis.ts").read_text(encoding="utf-8")
        self.assertIn("agent_memory.hooks.opencode", opencode_plugin)

    def test_init_merges_existing_json(self) -> None:
        (self.root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"existing": {"command": "echo"}}}),
            encoding="utf-8",
        )
        claude_dir = self.root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text(
            json.dumps({"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo existing"}]}]}}),
            encoding="utf-8",
        )

        config = InitConfig(
            workspace_root=self.root,
            python_executable="/usr/bin/python3",
            db_path=self.root / ".anamnesis" / "anamnesis.db",
            codex_home=self.root / ".fake-codex",
        )
        InitService(config).run()

        mcp = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertIn("existing", mcp["mcpServers"])
        self.assertIn("anamnesis", mcp["mcpServers"])

        settings = json.loads((claude_dir / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(len(settings["hooks"]["SessionEnd"]), 2)

    def test_register_codex_uses_parent_home_for_dot_codex_path(self) -> None:
        codex_home = self.root / ".codex"
        config = InitConfig(
            workspace_root=self.root,
            python_executable="/usr/bin/python3",
            db_path=self.root / ".anamnesis" / "anamnesis.db",
            codex_home=codex_home,
            register_codex=True,
            clients=("codex",),
        )
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            InitService(config).run()
        self.assertEqual(run.call_count, 2)
        remove_call, add_call = run.call_args_list
        self.assertEqual(remove_call.args[0][:4], ["codex", "mcp", "remove", "anamnesis"])
        self.assertEqual(add_call.args[0][:4], ["codex", "mcp", "add", "anamnesis"])
        self.assertEqual(add_call.kwargs["env"]["HOME"], str(self.root))

    def test_register_codex_rejects_non_dot_codex_home(self) -> None:
        config = InitConfig(
            workspace_root=self.root,
            python_executable="/usr/bin/python3",
            db_path=self.root / ".anamnesis" / "anamnesis.db",
            codex_home=self.root / ".fake-codex",
            register_codex=True,
            clients=("codex",),
        )
        with self.assertRaisesRegex(ValueError, "--register-codex requires --codex-home"):
            InitService(config).run()


if __name__ == "__main__":
    unittest.main()
