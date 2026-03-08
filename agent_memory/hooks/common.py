from __future__ import annotations

from agent_memory.ingest import main as ingest_main


def run(agent: str) -> None:
    ingest_main(default_agent=agent)
