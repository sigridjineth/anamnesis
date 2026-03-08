from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory.config import Settings
from agent_memory.providers.flex import FlexCellProvider
from agent_memory.providers.uqa import UQASidecarProvider


@dataclass(slots=True)
class FlexToUQASync:
    flex: FlexCellProvider
    uqa: UQASidecarProvider

    def needs_sync(self) -> bool:
        source = self.flex.resolve_cell_path()
        target = self.uqa.db_path
        if not target.exists():
            return True
        return source.exists() and source.stat().st_mtime > target.stat().st_mtime

    def sync(self, force: bool = False, limit: int | None = None) -> dict[str, Any]:
        if not force and not self.needs_sync():
            return {
                "synced": False,
                "reason": "sidecar_up_to_date",
                "source": str(self.flex.resolve_cell_path()),
                "target": str(self.uqa.db_path),
            }
        records = self.flex.fetch_sync_records(limit=limit)
        result = self.uqa.rebuild_memory_events(records)
        return {
            "synced": True,
            "indexed_rows": result["indexed_rows"],
            "source": str(self.flex.resolve_cell_path()),
            "target": str(self.uqa.db_path),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Project a Flex cell into a UQA sidecar")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    flex = FlexCellProvider(
        cell_name=settings.flex_cell,
        cell_path=settings.flex_cell_path,
        repo_root=settings.flex_repo_root,
    )
    uqa = UQASidecarProvider(settings.uqa_sidecar_path, repo_root=settings.uqa_repo_root)
    print(FlexToUQASync(flex=flex, uqa=uqa).sync(force=args.force, limit=args.limit))


if __name__ == "__main__":
    main()
