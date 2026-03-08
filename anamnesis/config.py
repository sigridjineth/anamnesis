from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return None


def _existing(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.exists() else None


def ensure_repo_on_syspath(path: Path | None) -> None:
    if path is None:
        return
    repo = str(path.resolve())
    if repo not in sys.path:
        sys.path.insert(0, repo)


@dataclass(slots=True)
class Settings:
    workspace_root: Path
    raw_db_path: Path
    uqa_sidecar_path: Path
    uqa_repo_root: Path | None
    default_limit: int = 10

    @classmethod
    def from_env(cls, workspace_root: Path | None = None) -> "Settings":
        root = (workspace_root or Path(__file__).resolve().parents[1]).resolve()
        raw_db = Path(
            _first_env("ANAMNESIS_DB") or (root / ".anamnesis" / "anamnesis.db")
        ).expanduser().resolve()
        sidecar = Path(
            _first_env("ANAMNESIS_UQA_SIDECAR") or raw_db.with_suffix(".uqa.db")
        ).expanduser().resolve()
        uqa_repo = (
            _existing(Path(os.environ["UQA_REPO_ROOT"]).expanduser().resolve())
            if os.environ.get("UQA_REPO_ROOT")
            else _existing(root / "uqa")
        )
        default_limit = int(_first_env("ANAMNESIS_LIMIT") or "10")
        return cls(
            workspace_root=root,
            raw_db_path=raw_db,
            uqa_sidecar_path=sidecar,
            uqa_repo_root=uqa_repo,
            default_limit=default_limit,
        )


WorkspaceConfig = Settings
