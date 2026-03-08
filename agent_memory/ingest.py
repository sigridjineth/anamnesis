from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent_memory.adapters import ClaudeAdapter, CodexAdapter, OpenCodeAdapter
from agent_memory.storage import RawMemoryStore


ADAPTERS = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "opencode": OpenCodeAdapter,
}


def get_adapter(agent: str):
    key = agent.lower()
    try:
        return ADAPTERS[key]()
    except KeyError as exc:
        raise ValueError(
            f"Unsupported agent '{agent}'. Expected one of: {sorted(ADAPTERS)}"
        ) from exc


def load_payloads(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    else:
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [dict(parsed)]
        raise ValueError("JSON payload must be an object or array of objects")

    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("JSONL payloads must contain one object per line")
        payloads.append(dict(parsed))
    return payloads


def apply_overrides(
    payloads: Iterable[dict[str, Any]],
    *,
    session_id: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for payload in payloads:
        item = dict(payload)
        if session_id and not any(
            item.get(key) for key in ("session_id", "sessionId", "conversation_id")
        ):
            item["session_id"] = session_id
        if project_id and not any(
            item.get(key) for key in ("project_id", "projectId", "project", "cwd")
        ):
            item["project_id"] = project_id
        updated.append(item)
    return updated


@dataclass(slots=True)
class IngestionService:
    store: RawMemoryStore

    def ingest(self, agent: str, payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
        adapter = get_adapter(agent)
        result = self.store.append_payloads(adapter, payloads)
        return {
            "agent": agent,
            "db_path": str(self.store.db_path),
            "payloads": result["payloads"],
            "events": result["events"],
        }


def _read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def main(default_agent: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Normalize agent hook payloads and append them to the raw memory store"
    )
    parser.add_argument("--agent", default=default_agent, choices=sorted(ADAPTERS))
    parser.add_argument("--db", required=True, help="Path to the raw memory SQLite database")
    parser.add_argument(
        "--input",
        help="Optional path to a JSON/JSONL payload file. Reads stdin when omitted.",
    )
    parser.add_argument("--session-id")
    parser.add_argument("--project-id")
    parser.add_argument("--quiet", action="store_true", help="Suppress summary output")
    args = parser.parse_args()

    if not args.agent:
        raise SystemExit("--agent is required")

    payloads = load_payloads(_read_input(args.input))
    payloads = apply_overrides(
        payloads,
        session_id=args.session_id,
        project_id=args.project_id,
    )
    service = IngestionService(RawMemoryStore(args.db))
    summary = service.ingest(args.agent, payloads)
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
