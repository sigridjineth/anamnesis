from __future__ import annotations

from pathlib import Path

from .config import ensure_repo_on_syspath


WORKSPACE_MARKERS: tuple[str, ...] = (
    ".anamnesis",
    ".git",
    ".mcp.json",
    ".claude",
    ".opencode",
)


def discover_workspace_root(start_path: str | Path | None = None) -> Path:
    candidate = Path(start_path or Path.cwd()).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve(strict=False)
    else:
        candidate = candidate.resolve(strict=False)
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent

    for current in (candidate, *candidate.parents):
        if any((current / marker).exists() for marker in WORKSPACE_MARKERS):
            return current
    return candidate


def workspace_db_path(workspace_root: str | Path | None = None) -> Path:
    root = discover_workspace_root(workspace_root)
    return root / ".anamnesis" / "anamnesis.db"


__all__ = ["ensure_repo_on_syspath", "discover_workspace_root", "workspace_db_path", "WORKSPACE_MARKERS"]
