"""Anamnesis shared memory interface for UQA, Flex, Claude, Codex, and OpenCode."""

from typing import TYPE_CHECKING

from agent_memory.config import Settings
from agent_memory.models import CanonicalEvent
from agent_memory.query import MemoryQueryService
from agent_memory.service import MemoryService
from agent_memory.storage import RawMemoryStore

if TYPE_CHECKING:  # pragma: no cover
    from agent_memory.init_cli import InitConfig, InitService

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
        from agent_memory.init_cli import InitConfig, InitService

        return {"InitConfig": InitConfig, "InitService": InitService}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
