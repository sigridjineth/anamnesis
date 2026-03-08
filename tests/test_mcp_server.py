from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from anamnesis import mcp_server


class DummyService:
    def health(self):
        return {"ok": True}

    def orient(self, **kwargs):
        return {"tool": "orient", **kwargs}

    def search(self, query, **kwargs):
        return {"tool": "search", "query": query, **kwargs}

    def trace_file(self, path, **kwargs):
        return {"tool": "trace_file", "path": path, **kwargs}

    def trace_decision(self, query, **kwargs):
        return {"tool": "trace_decision", "query": query, **kwargs}

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
        self.run_calls: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def tool(self):
        def decorator(fn):
            self.registered_tools.append(fn.__name__)
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
                "memory_digest",
                "memory_health",
                "memory_orient",
                "memory_rebuild_uqa_sidecar",
                "memory_search",
                "memory_sql",
                "memory_trace_decision",
                "memory_trace_file",
            ],
        )
        self.assertEqual(
            instance.run_calls,
            [{"transport": "sse", "mount_path": "/anamnesis"}],
        )


if __name__ == "__main__":
    unittest.main()
