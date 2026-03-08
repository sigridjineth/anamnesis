from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from anamnesis import mcp_server


class DummyService:
    def __init__(self, *args, **kwargs):
        pass

    def health(self):
        return {"ok": True}

    def orient(self, **kwargs):
        return {"tool": "orient", **kwargs}

    def search(self, query, **kwargs):
        return {"tool": "search", "query": query, **kwargs}

    def file_search(self, query, **kwargs):
        return {"tool": "file_search", "query": query, **kwargs}

    def trace_file(self, path, **kwargs):
        return {"tool": "trace_file", "path": path, **kwargs}

    def trace_decision(self, query, **kwargs):
        return {"tool": "trace_decision", "query": query, **kwargs}

    def story(self, **kwargs):
        return {"tool": "story", **kwargs}

    def sprints(self, **kwargs):
        return {"tool": "sprints", **kwargs}

    def genealogy(self, query, **kwargs):
        return {"tool": "genealogy", "query": query, **kwargs}

    def bridges(self, query_a, query_b, **kwargs):
        return {"tool": "bridges", "query_a": query_a, "query_b": query_b, **kwargs}

    def delegation_tree(self, **kwargs):
        return {"tool": "delegation_tree", **kwargs}

    def digest(self, **kwargs):
        return {"tool": "digest", **kwargs}

    def sql(self, sql, **kwargs):
        return {"tool": "sql", "sql": sql, **kwargs}

    def rebuild_uqa_sidecar(self, **kwargs):
        return {"tool": "rebuild_uqa_sidecar", **kwargs}


class FakeFastMCP:
    instances: list["FakeFastMCP"] = []

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.registered_tools: list[str] = []
        self.tool_fns: dict[str, object] = {}
        self.run_calls: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def tool(self):
        def decorator(fn):
            self.registered_tools.append(fn.__name__)
            self.tool_fns[fn.__name__] = fn
            return fn

        return decorator

    def run(self, transport: str = "stdio", mount_path: str | None = None) -> None:
        self.run_calls.append({"transport": transport, "mount_path": mount_path})


def _fake_fastmcp_modules() -> dict[str, types.ModuleType]:
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    mcp_module.server = server_module
    server_module.fastmcp = fastmcp_module
    return {
        "mcp": mcp_module,
        "mcp.server": server_module,
        "mcp.server.fastmcp": fastmcp_module,
    }


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeFastMCP.instances.clear()

    def test_build_server_config_defaults_to_stdio(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = mcp_server.build_server_config([])

        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8000)
        self.assertEqual(config.streamable_http_path, "/mcp")

    def test_build_server_config_uses_http_friendly_defaults(self) -> None:
        with patch.dict(os.environ, {"PORT": "9123"}, clear=True):
            config = mcp_server.build_server_config(["--transport", "streamable-http"])

        self.assertEqual(config.transport, "streamable-http")
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9123)

    def test_build_server_config_rejects_invalid_boolean_env(self) -> None:
        with patch.dict(os.environ, {"ANAMNESIS_MCP_DEBUG": "maybe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "ANAMNESIS_MCP_DEBUG"):
                mcp_server.build_server_config([])

    def test_main_runs_server_with_requested_transport(self) -> None:
        with (
            patch.dict(sys.modules, _fake_fastmcp_modules()),
            patch.object(mcp_server, "MemoryService", DummyService),
            patch.dict(os.environ, {}, clear=True),
        ):
            mcp_server.main(
                [
                    "--transport",
                    "sse",
                    "--mount-path",
                    "/anamnesis",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8123",
                    "--debug",
                ]
            )

        [instance] = FakeFastMCP.instances
        self.assertEqual(instance.name, "anamnesis")
        self.assertEqual(instance.kwargs["host"], "0.0.0.0")
        self.assertEqual(instance.kwargs["port"], 8123)
        self.assertEqual(instance.kwargs["mount_path"], "/anamnesis")
        self.assertTrue(instance.kwargs["debug"])
        self.assertEqual(
            sorted(instance.registered_tools),
            [
                "anamnesis_search",
                "memory_bridges",
                "memory_delegation_tree",
                "memory_digest",
                "memory_file_search",
                "memory_genealogy",
                "memory_health",
                "memory_orient",
                "memory_rebuild_uqa_sidecar",
                "memory_search",
                "memory_sprints",
                "memory_sql",
                "memory_story",
                "memory_trace_decision",
                "memory_trace_file",
            ],
        )
        self.assertEqual(
            instance.run_calls,
            [{"transport": "sse", "mount_path": "/anamnesis"}],
        )

    def test_project_scoped_tools_forward_project_id(self) -> None:
        with (
            patch.dict(sys.modules, _fake_fastmcp_modules()),
            patch.object(mcp_server, "MemoryService", DummyService),
        ):
            mcp_server.create_server()

        [instance] = FakeFastMCP.instances
        trace = json.loads(instance.tool_fns["memory_trace_file"]("README.md", project_id="/repo/app"))
        story = json.loads(instance.tool_fns["memory_story"](session_id="ses-1", project_id="/repo/app"))
        digest = json.loads(instance.tool_fns["memory_digest"](project_id="/repo/app"))

        self.assertEqual(trace["project_id"], "/repo/app")
        self.assertEqual(story["project_id"], "/repo/app")
        self.assertEqual(digest["project_id"], "/repo/app")

    def test_anamnesis_search_tool_routes_query_cell_and_params(self) -> None:
        with (
            patch.dict(sys.modules, _fake_fastmcp_modules()),
            patch.object(mcp_server, "MemoryService", DummyService),
            patch("anamnesis.cli.execute_mcp_query_text", return_value="[1 rows, ~1 tok]\n[]") as execute_mcp_query_text,
        ):
            mcp_server.create_server()
            [instance] = FakeFastMCP.instances
            result = instance.tool_fns["anamnesis_search"](
                "@chronicle",
                cell="claude_code",
                params={"session": "ses-1"},
            )
            self.assertEqual(result, "[1 rows, ~1 tok]\n[]")
            execute_mcp_query_text.assert_called_once_with(
                "@chronicle",
                cell="claude_code",
                params={"session": "ses-1"},
            )

    def test_create_server_uses_current_working_directory_for_default_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = types.SimpleNamespace(
                workspace_root=tmp,
                raw_db_path=os.path.join(tmp, ".anamnesis", "anamnesis.db"),
                uqa_sidecar_path=os.path.join(tmp, ".anamnesis", "anamnesis.uqa.db"),
                uqa_repo_root=None,
            )
            with (
                patch.dict(sys.modules, _fake_fastmcp_modules()),
                patch.object(mcp_server, "MemoryService") as memory_service,
                patch.object(mcp_server.Settings, "from_env", return_value=workspace) as from_env,
                patch("pathlib.Path.cwd", return_value=Path(tmp)),
            ):
                mcp_server.create_server()

            from_env.assert_called_once_with(workspace_root=Path(tmp))
            memory_service.assert_called_once_with(settings=workspace)


if __name__ == "__main__":
    unittest.main()
