from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from agent_memory.service import MemoryService


Transport = Literal["stdio", "sse", "streamable-http"]
TRANSPORTS: tuple[Transport, ...] = ("stdio", "sse", "streamable-http")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of: 1, 0, true, false, yes, no, on, off"
    )


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return None


def _env_flag_alias(names: Sequence[str], default: bool = False) -> bool:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None:
            return _env_flag(name, default=default)
    return default


def _env_int(*names: str) -> int | None:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    return None


def _default_host(transport: Transport) -> str:
    return "127.0.0.1" if transport == "stdio" else "0.0.0.0"


@dataclass(slots=True)
class MCPServerConfig:
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    mount_path: str = "/"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    streamable_http_path: str = "/mcp"
    debug: bool = False
    log_level: str = "INFO"
    json_response: bool = False
    stateless_http: bool = False


def build_server_config(argv: Sequence[str] | None = None) -> MCPServerConfig:
    env_transport = (_env_first("ANAMNESIS_MCP_TRANSPORT", "AGENT_MEMORY_MCP_TRANSPORT") or "stdio").strip().lower()
    if env_transport not in TRANSPORTS:
        raise ValueError(
            f"ANAMNESIS_MCP_TRANSPORT/AGENT_MEMORY_MCP_TRANSPORT must be one of: {', '.join(TRANSPORTS)}"
        )

    env_log_level = (_env_first("ANAMNESIS_MCP_LOG_LEVEL", "AGENT_MEMORY_MCP_LOG_LEVEL") or "INFO").strip().upper()
    if env_log_level not in LOG_LEVELS:
        raise ValueError(
            f"ANAMNESIS_MCP_LOG_LEVEL/AGENT_MEMORY_MCP_LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}"
        )

    parser = argparse.ArgumentParser(
        description="Run the Anamnesis MCP server locally or over HTTP transports"
    )
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default=env_transport,
        help="MCP transport to run. Defaults to ANAMNESIS_MCP_TRANSPORT, then AGENT_MEMORY_MCP_TRANSPORT, then stdio.",
    )
    parser.add_argument(
        "--host",
        help="Bind host for HTTP transports. Defaults to ANAMNESIS_MCP_HOST, then AGENT_MEMORY_MCP_HOST, then HOST, then a transport-specific default.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Bind port for HTTP transports. Defaults to ANAMNESIS_MCP_PORT, then AGENT_MEMORY_MCP_PORT, then PORT, then 8000.",
    )
    parser.add_argument(
        "--mount-path",
        default=_env_first("ANAMNESIS_MCP_MOUNT_PATH", "AGENT_MEMORY_MCP_MOUNT_PATH") or "/",
        help="Base mount path used by the SSE transport.",
    )
    parser.add_argument(
        "--sse-path",
        default=_env_first("ANAMNESIS_MCP_SSE_PATH", "AGENT_MEMORY_MCP_SSE_PATH") or "/sse",
        help="SSE endpoint path.",
    )
    parser.add_argument(
        "--message-path",
        default=_env_first("ANAMNESIS_MCP_MESSAGE_PATH", "AGENT_MEMORY_MCP_MESSAGE_PATH") or "/messages/",
        help="Message POST path paired with SSE transport.",
    )
    parser.add_argument(
        "--streamable-http-path",
        default=_env_first("ANAMNESIS_MCP_STREAMABLE_HTTP_PATH", "AGENT_MEMORY_MCP_STREAMABLE_HTTP_PATH") or "/mcp",
        help="Streamable HTTP endpoint path.",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=env_log_level,
        help="Server log level.",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=_env_flag_alias(("ANAMNESIS_MCP_DEBUG", "AGENT_MEMORY_MCP_DEBUG"), False),
        help="Enable FastMCP debug mode.",
    )
    parser.add_argument(
        "--json-response",
        action=argparse.BooleanOptionalAction,
        default=_env_flag_alias(("ANAMNESIS_MCP_JSON_RESPONSE", "AGENT_MEMORY_MCP_JSON_RESPONSE"), False),
        help="Enable JSON responses for HTTP transports when supported by the SDK.",
    )
    parser.add_argument(
        "--stateless-http",
        action=argparse.BooleanOptionalAction,
        default=_env_flag_alias(("ANAMNESIS_MCP_STATELESS_HTTP", "AGENT_MEMORY_MCP_STATELESS_HTTP"), False),
        help="Enable stateless HTTP mode when using streamable HTTP.",
    )
    args = parser.parse_args(argv)

    transport = args.transport
    host = (
        args.host
        or _env_first("ANAMNESIS_MCP_HOST", "AGENT_MEMORY_MCP_HOST")
        or os.environ.get("HOST")
        or _default_host(transport)
    )
    port = args.port if args.port is not None else (_env_int("ANAMNESIS_MCP_PORT", "AGENT_MEMORY_MCP_PORT", "PORT") or 8000)

    return MCPServerConfig(
        transport=transport,
        host=host,
        port=port,
        mount_path=args.mount_path,
        sse_path=args.sse_path,
        message_path=args.message_path,
        streamable_http_path=args.streamable_http_path,
        debug=args.debug,
        log_level=args.log_level,
        json_response=args.json_response,
        stateless_http=args.stateless_http,
    )


def create_server(config: MCPServerConfig | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("The MCP SDK is not installed. Install with: pip install '.[mcp]'") from exc

    config = config or MCPServerConfig()
    service = MemoryService()
    mcp = FastMCP(
        "anamnesis",
        debug=config.debug,
        log_level=config.log_level,
        host=config.host,
        port=config.port,
        mount_path=config.mount_path,
        sse_path=config.sse_path,
        message_path=config.message_path,
        streamable_http_path=config.streamable_http_path,
        json_response=config.json_response,
        stateless_http=config.stateless_http,
    )

    @mcp.tool()
    def memory_health() -> str:
        return _json(service.health())

    @mcp.tool()
    def memory_orient(db_path: str | None = None, project_id: str | None = None) -> str:
        return _json(service.orient(db_path=db_path, project_id=project_id))

    @mcp.tool()
    def memory_search(query: str, db_path: str | None = None, limit: int = 10, project_id: str | None = None, backend: str = "auto") -> str:
        return _json(service.search(query, db_path=db_path, limit=limit, project_id=project_id, backend=backend))

    @mcp.tool()
    def memory_trace_file(path: str, db_path: str | None = None, limit: int = 20) -> str:
        return _json(service.trace_file(path, db_path=db_path, limit=limit))

    @mcp.tool()
    def memory_trace_decision(query: str, db_path: str | None = None, limit: int = 10) -> str:
        return _json(service.trace_decision(query, db_path=db_path, limit=limit))

    @mcp.tool()
    def memory_digest(days: int = 7, db_path: str | None = None) -> str:
        return _json(service.digest(days=days, db_path=db_path))

    @mcp.tool()
    def memory_sql(sql: str, db_path: str | None = None, read_only: bool = True) -> str:
        return _json(service.sql(sql, db_path=db_path, read_only=read_only))

    @mcp.tool()
    def memory_rebuild_uqa_sidecar(db_path: str | None = None, sidecar_path: str | None = None) -> str:
        return _json(service.rebuild_uqa_sidecar(db_path=db_path, sidecar_path=sidecar_path))

    return mcp


def main(argv: Sequence[str] | None = None) -> None:
    config = build_server_config(argv)
    server = create_server(config)
    mount_path = config.mount_path if config.transport == "sse" else None
    server.run(transport=config.transport, mount_path=mount_path)


if __name__ == "__main__":  # pragma: no cover
    main()
