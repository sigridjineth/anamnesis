from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    workspace = here.parents[1]
    return [workspace, Path.cwd()]


def ensure_checkout_on_path(name: str) -> Path | None:
    for root in _candidate_roots():
        candidate = root / name
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return candidate
        if candidate.exists():
            return candidate
    return None


def optional_import(module_name: str, checkout_name: str | None = None) -> ModuleType | None:
    checkout = checkout_name or module_name.split('.', 1)[0]
    ensure_checkout_on_path(checkout)
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None
