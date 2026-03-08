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
    uqa_sidecar_path: Path
    flex_repo_root: Path | None
    uqa_repo_root: Path | None
    flex_cell: str
    flex_cell_path: Path | None
    default_limit: int = 10
    raw_db_path: Path | None = None

    def __post_init__(self) -> None:
        if self.raw_db_path is None:
            self.raw_db_path = (self.workspace_root / ".anamnesis" / "anamnesis.db").resolve()

    @classmethod
    def from_env(cls, workspace_root: Path | None = None) -> "Settings":
        root = (workspace_root or Path(__file__).resolve().parents[1]).resolve()
        raw_db_value = _first_env("ANAMNESIS_DB", "AGENT_MEMORY_DB")
        raw_db = Path(raw_db_value or (root / ".anamnesis" / "anamnesis.db")).expanduser().resolve()
        sidecar_value = _first_env("ANAMNESIS_UQA_SIDECAR", "AGENT_MEMORY_UQA_SIDECAR")
        sidecar = Path(sidecar_value or raw_db.with_suffix('.uqa.db')).expanduser().resolve()
        flex_repo = _existing(Path(os.environ["FLEX_REPO_ROOT"]).expanduser().resolve()) if os.environ.get("FLEX_REPO_ROOT") else _existing(root / "flex")
        uqa_repo = _existing(Path(os.environ["UQA_REPO_ROOT"]).expanduser().resolve()) if os.environ.get("UQA_REPO_ROOT") else _existing(root / "uqa")
        flex_cell_path = _existing(Path(os.environ["FLEX_CELL_PATH"]).expanduser().resolve()) if os.environ.get("FLEX_CELL_PATH") else None
        return cls(
            workspace_root=root,
            raw_db_path=raw_db,
            uqa_sidecar_path=sidecar,
            flex_repo_root=flex_repo,
            uqa_repo_root=uqa_repo,
            flex_cell=os.environ.get("FLEX_CELL", "claude_code"),
            flex_cell_path=flex_cell_path,
            default_limit=int(_first_env("ANAMNESIS_LIMIT", "AGENT_MEMORY_LIMIT") or "10"),
        )

    @classmethod
    def from_workspace(cls, workspace_root: Path | None = None) -> "Settings":
        """Compatibility shim for older modules in this workspace."""
        return cls.from_env(workspace_root=workspace_root)

    # Compatibility aliases for older modules in this workspace.
    @property
    def flex_repo(self) -> Path | None:
        return self.flex_repo_root

    @property
    def uqa_repo(self) -> Path | None:
        return self.uqa_repo_root

    @property
    def default_flex_cell(self) -> str:
        return self.flex_cell

    @property
    def default_flex_db_path(self) -> Path | None:
        return self.flex_cell_path

    @property
    def default_uqa_db_path(self) -> Path:
        return self.uqa_sidecar_path


WorkspaceConfig = Settings
