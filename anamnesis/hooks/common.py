from __future__ import annotations

from anamnesis.ingest import main as ingest_main


def run(agent: str) -> None:
    ingest_main(default_agent=agent)
