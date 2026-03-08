from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CLIENTS = ("claude", "codex", "opencode")


def _json_text(data: Any) -> str:
    return json.dumps(data, indent=2) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _write_text(path: Path, text: str, *, force: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == text:
            return "unchanged"
        if not force:
            raise FileExistsError(f"Refusing to overwrite existing file without --force: {path}")
        status = "overwritten"
    else:
        status = "created"
    path.write_text(text, encoding="utf-8")
    return status


def _write_json(path: Path, data: dict[str, Any]) -> str:
    return _write_text(path, _json_text(data), force=True)


def _ensure_hook_block(
    container: dict[str, Any],
    event_name: str,
    command: str,
    *,
    matcher: str | None = None,
    timeout: int | None = None,
) -> None:
    hooks = container.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings file must contain an object-shaped 'hooks' field")
    blocks = hooks.setdefault(event_name, [])
    if not isinstance(blocks, list):
        raise ValueError(f"hooks.{event_name} must be a list")

    hook_item: dict[str, Any] = {
        "type": "command",
        "command": command,
    }
    if timeout is not None:
        hook_item["timeout"] = timeout

    block: dict[str, Any] = {"hooks": [hook_item]}
    if matcher is not None:
        block["matcher"] = matcher

    for existing in blocks:
        if not isinstance(existing, dict):
            continue
        if matcher is not None and existing.get("matcher") != matcher:
            continue
        existing_hooks = existing.get("hooks")
        if not isinstance(existing_hooks, list):
            continue
        for item in existing_hooks:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "command" and item.get("command") == command:
                if timeout is not None and item.get("timeout") != timeout:
                    item["timeout"] = timeout
                return
    blocks.append(block)


def _append_gitignore_entry(root: Path, entry: str) -> str:
    path = root / ".gitignore"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    if entry in lines:
        return "unchanged"
    lines.append(entry)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return "updated"


def _find_optional_repo(workspace_root: Path, name: str, explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidate = (workspace_root / name).resolve()
    return candidate if candidate.exists() else None


def _render_codex_mcp_add_command(
    *,
    python_executable: str,
    db_path: Path,
    flex_repo_root: Path | None,
    uqa_repo_root: Path | None,
) -> list[str]:
    command = [
        "codex",
        "mcp",
        "add",
        "anamnesis",
        "--env",
        f"ANAMNESIS_DB={db_path}",
    ]
    if flex_repo_root is not None:
        command.extend(["--env", f"FLEX_REPO_ROOT={flex_repo_root}"])
    if uqa_repo_root is not None:
        command.extend(["--env", f"UQA_REPO_ROOT={uqa_repo_root}"])
    command.extend(["--env", "FLEX_CELL=claude_code", "--", python_executable, "-m", "agent_memory.mcp_server"])
    return command


@dataclass(slots=True)
class InitConfig:
    workspace_root: Path
    python_executable: str
    db_path: Path
    clients: tuple[str, ...] = DEFAULT_CLIENTS
    force: bool = False
    codex_home: Path = field(default_factory=lambda: Path.home() / ".codex")
    register_codex: bool = False
    flex_repo_root: Path | None = None
    uqa_repo_root: Path | None = None

    @property
    def mcp_env(self) -> dict[str, str]:
        env = {"ANAMNESIS_DB": str(self.db_path), "FLEX_CELL": "claude_code"}
        if self.flex_repo_root is not None:
            env["FLEX_REPO_ROOT"] = str(self.flex_repo_root)
        if self.uqa_repo_root is not None:
            env["UQA_REPO_ROOT"] = str(self.uqa_repo_root)
        return env


class InitService:
    def __init__(self, config: InitConfig):
        self.config = config

    def run(self) -> dict[str, Any]:
        files: dict[str, str] = {}
        files[str(self.config.workspace_root / ".gitignore")] = _append_gitignore_entry(
            self.config.workspace_root,
            ".anamnesis/",
        )
        if "claude" in self.config.clients:
            files.update(self._init_claude())
        if "codex" in self.config.clients:
            files.update(self._init_codex())
        if "opencode" in self.config.clients:
            files.update(self._init_opencode())
        return {
            "workspace_root": str(self.config.workspace_root),
            "db_path": str(self.config.db_path),
            "python_executable": self.config.python_executable,
            "clients": list(self.config.clients),
            "files": files,
            "codex_registered": bool(self.config.register_codex and "codex" in self.config.clients),
        }

    def _init_claude(self) -> dict[str, str]:
        mcp_path = self.config.workspace_root / ".mcp.json"
        settings_path = self.config.workspace_root / ".claude" / "settings.local.json"

        mcp = _load_json(mcp_path)
        servers = mcp.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(".mcp.json must contain an object-shaped 'mcpServers' field")
        servers["anamnesis"] = {
            "command": self.config.python_executable,
            "args": ["-m", "agent_memory.mcp_server"],
            "cwd": str(self.config.workspace_root),
            "env": self.config.mcp_env,
        }

        command = (
            f"cd \"$CLAUDE_PROJECT_DIR\" && {shlex.quote(self.config.python_executable)} "
            f"-m agent_memory.hooks.claude --db \"$CLAUDE_PROJECT_DIR/.anamnesis/anamnesis.db\" --quiet"
        )
        settings = _load_json(settings_path)
        _ensure_hook_block(settings, "UserPromptSubmit", command)
        _ensure_hook_block(settings, "PreToolUse", command, matcher="*")
        _ensure_hook_block(settings, "PostToolUse", command, matcher="*")
        _ensure_hook_block(settings, "SessionEnd", command)

        return {
            str(mcp_path): _write_json(mcp_path, mcp),
            str(settings_path): _write_json(settings_path, settings),
        }

    def _init_codex(self) -> dict[str, str]:
        settings_path = self.config.codex_home / "settings.json"
        settings = _load_json(settings_path)
        command = (
            f"{shlex.quote(self.config.python_executable)} -m agent_memory.hooks.codex "
            f"--db {shlex.quote(str(self.config.db_path))} --quiet"
        )
        _ensure_hook_block(settings, "UserPromptSubmit", command, timeout=5)
        _ensure_hook_block(settings, "PostToolUse", command, timeout=5)

        register_command = _render_codex_mcp_add_command(
            python_executable=self.config.python_executable,
            db_path=self.config.db_path,
            flex_repo_root=self.config.flex_repo_root,
            uqa_repo_root=self.config.uqa_repo_root,
        )
        script_path = self.config.workspace_root / ".anamnesis" / "generated" / "register-codex-mcp.sh"
        script_text = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "codex mcp remove anamnesis >/dev/null 2>&1 || true\n"
            f"{shlex.join(register_command)}\n"
        )

        result = {
            str(settings_path): _write_json(settings_path, settings),
            str(script_path): _write_text(script_path, script_text, force=True),
        }
        script_path.chmod(0o755)

        if self.config.register_codex:
            if self.config.codex_home.name != ".codex":
                raise ValueError("--register-codex requires --codex-home to point at a .codex directory")
            env = os.environ.copy()
            env["HOME"] = str(self.config.codex_home.parent)
            subprocess.run(["codex", "mcp", "remove", "anamnesis"], check=False, cwd=self.config.workspace_root, env=env)
            subprocess.run(register_command, check=True, cwd=self.config.workspace_root, env=env)

        return result

    def _init_opencode(self) -> dict[str, str]:
        config_path = self.config.workspace_root / ".opencode" / "opencode.json"
        plugin_path = self.config.workspace_root / ".opencode" / "plugins" / "anamnesis.ts"

        config = _load_json(config_path)
        mcp = config.setdefault("mcp", {})
        if not isinstance(mcp, dict):
            raise ValueError(".opencode/opencode.json must contain an object-shaped 'mcp' field")
        mcp["anamnesis"] = {
            "command": self.config.python_executable,
            "args": ["-m", "agent_memory.mcp_server"],
            "env": self.config.mcp_env,
        }
        notes = config.setdefault("_notes", [])
        if isinstance(notes, list):
            note = "Place the live capture plugin at .opencode/plugins/anamnesis.ts"
            if note not in notes:
                notes.append(note)

        return {
            str(config_path): _write_json(config_path, config),
            str(plugin_path): _write_text(plugin_path, self._render_opencode_plugin(), force=self.config.force),
        }

    def _render_opencode_plugin(self) -> str:
        python = json.dumps(self.config.python_executable)
        return f"""import type {{ Plugin }} from "@opencode-ai/plugin"

const python = {python}

export const AnamnesisPlugin: Plugin = async ({{ $, directory }}) => {{
  const dbPath = `${{directory}}/.anamnesis/anamnesis.db`

  const emit = async (payload: Record<string, unknown>) => {{
    const body = JSON.stringify({{
      ...payload,
      cwd: directory,
      projectId: directory,
      ts: Date.now(),
    }})
    await $`mkdir -p ${{`${{directory}}/.anamnesis`}}`
    await $`printf '%s\\n' ${{body}} | ${{python}} -m agent_memory.hooks.opencode --db ${{dbPath}} --quiet`
  }}

  return {{
    "chat.message": async (input, output) => {{
      await emit({{
        type: "chat.message",
        sessionID: input.sessionID,
        agent: input.agent,
        model: input.model,
        message: output.message,
        parts: output.parts,
      }})
    }},
    "tool.execute.before": async (input, output) => {{
      await emit({{
        type: "tool.execute.before",
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        args: output.args,
      }})
    }},
    "tool.execute.after": async (input, output) => {{
      await emit({{
        type: "tool.execute.after",
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        title: output.title,
        output: output.output,
        metadata: output.metadata,
      }})
    }},
    event: async ({{ event }}) => {{
      if (
        event.type === "message.part.updated" ||
        event.type === "file.edited" ||
        event.type === "session.idle"
      ) {{
        await emit(event as Record<string, unknown>)
      }}
    }},
  }}
}}

export default AnamnesisPlugin
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write deployable Claude/Codex/OpenCode configuration for Anamnesis"
    )
    parser.add_argument("--workspace-root", default=".", help="Target project root")
    parser.add_argument(
        "--client",
        dest="clients",
        action="append",
        choices=DEFAULT_CLIENTS,
        help="Client to scaffold. Repeat to restrict output; defaults to all.",
    )
    parser.add_argument("--db-path", help="Canonical SQLite database path")
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--register-codex", action="store_true")
    parser.add_argument("--flex-repo-root")
    parser.add_argument("--uqa-repo-root")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else (workspace_root / ".anamnesis" / "anamnesis.db").resolve()

    config = InitConfig(
        workspace_root=workspace_root,
        python_executable=str(Path(args.python_executable).expanduser().resolve()),
        db_path=db_path,
        clients=tuple(args.clients or DEFAULT_CLIENTS),
        force=args.force,
        codex_home=Path(args.codex_home).expanduser().resolve(),
        register_codex=args.register_codex,
        flex_repo_root=_find_optional_repo(workspace_root, "flex", args.flex_repo_root),
        uqa_repo_root=_find_optional_repo(workspace_root, "uqa", args.uqa_repo_root),
    )
    print(_json_text(InitService(config).run()), end="")


if __name__ == "__main__":  # pragma: no cover
    main()
