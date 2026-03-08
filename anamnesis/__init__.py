"""Anamnesis: UQA-native shared memory for Claude, Codex, and OpenCode."""

from typing import TYPE_CHECKING

from anamnesis.config import Settings
from anamnesis.models import CanonicalEvent
from anamnesis.query import MemoryQueryService
from anamnesis.service import MemoryService
from anamnesis.storage import RawMemoryStore

if TYPE_CHECKING:  # pragma: no cover
    from anamnesis.init_cli import InitConfig, InitService

__all__ = [
    "CanonicalEvent",
    "InitConfig",
    "InitService",
    "MemoryQueryService",
    "MemoryService",
    "RawMemoryStore",
    "Settings",
]


def __getattr__(name: str):
    if name in {"InitConfig", "InitService"}:
        from anamnesis.init_cli import InitConfig, InitService

        return {"InitConfig": InitConfig, "InitService": InitService}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
