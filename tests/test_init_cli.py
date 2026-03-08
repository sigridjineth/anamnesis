from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis.init_cli import InitConfig, InitService


class InitCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
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
        self.assertTrue((self.root / ".claude" / "skills" / "survey" / "SKILL.md").exists())
        self.assertTrue((self.root / ".agents" / "skills" / "chronicle" / "SKILL.md").exists())

        mcp = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["anamnesis"]["command"], "/usr/bin/python3")
        self.assertEqual(
            mcp["mcpServers"]["anamnesis"]["env"]["ANAMNESIS_DB"],
            str(self.root / ".anamnesis" / "anamnesis.db"),
        )
        self.assertEqual(
            mcp["mcpServers"]["anamnesis"]["env"]["UQA_REPO_ROOT"],
            str(self.root / "uqa"),
        )

        codex_settings = json.loads((codex_home / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("UserPromptSubmit", codex_settings["hooks"])
        self.assertIn("PostToolUse", codex_settings["hooks"])
        codex_commands = [
            hook["command"]
            for block in codex_settings["hooks"]["UserPromptSubmit"]
            for hook in block.get("hooks", [])
            if hook.get("type") == "command"
        ]
        self.assertTrue(any("anamnesis.hooks.codex --quiet" in command for command in codex_commands))
        self.assertFalse(any("anamnesis.hooks.codex --db" in command for command in codex_commands))

        claude_settings = json.loads((self.root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        claude_command = claude_settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn(str(self.root / ".anamnesis" / "anamnesis.db"), claude_command)

        register_script = (self.root / ".anamnesis" / "generated" / "register-codex-mcp.sh").read_text(encoding="utf-8")
        self.assertNotIn("ANAMNESIS_DB=", register_script)

        opencode_plugin = (self.root / ".opencode" / "plugins" / "anamnesis.ts").read_text(encoding="utf-8")
        self.assertIn("anamnesis.hooks.opencode", opencode_plugin)
        orient_skill = (self.root / ".claude" / "skills" / "survey" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("memory_orient()", orient_skill)

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

    def test_init_codex_prunes_stale_anamnesis_hook_commands_before_adding_current_one(self) -> None:
        codex_home = self.root / ".fake-codex"
        codex_home.mkdir()
        (codex_home / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/tmp/old/bin/python -m anamnesis.hooks.codex --db /tmp/old.db --quiet",
                                    },
                                    {
                                        "type": "command",
                                        "command": "echo keep-me",
                                    },
                                ]
                            }
                        ],
                        "PostToolUse": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/tmp/other/bin/python -m anamnesis.hooks.codex --db /tmp/other.db --quiet",
                                    }
                                ]
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        config = InitConfig(
            workspace_root=self.root,
            python_executable="/usr/bin/python3",
            db_path=self.root / ".anamnesis" / "anamnesis.db",
            codex_home=codex_home,
        )
        InitService(config).run()

        settings = json.loads((codex_home / "settings.json").read_text(encoding="utf-8"))
        user_commands = [
            hook["command"]
            for block in settings["hooks"]["UserPromptSubmit"]
            for hook in block.get("hooks", [])
            if hook.get("type") == "command"
        ]
        post_commands = [
            hook["command"]
            for block in settings["hooks"]["PostToolUse"]
            for hook in block.get("hooks", [])
            if hook.get("type") == "command"
        ]

        self.assertIn("echo keep-me", user_commands)
        self.assertEqual(sum("anamnesis.hooks.codex" in command for command in user_commands), 1)
        self.assertEqual(sum("anamnesis.hooks.codex" in command for command in post_commands), 1)
        self.assertTrue(any("/usr/bin/python3 -m anamnesis.hooks.codex --quiet" in command for command in user_commands))

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
