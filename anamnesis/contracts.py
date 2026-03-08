from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from anamnesis.models import CanonicalEvent

AgentKind = Literal["claude", "codex", "opencode"]
BackendKind = Literal["uqa"]
EventKind = Literal[
    "prompt",
    "assistant_message",
    "tool_call",
    "tool_result",
    "permission",
    "file_touch",
    "session_state",
]


@dataclass(slots=True)
class SchemaObject:
    name: str
    kind: str
    columns: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SchemaSummary:
    backend: str
    target: str | None
    objects: list[SchemaObject] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchHit:
    source: str
    row: dict[str, Any]
    preview: str | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchResponse:
    backend: str
    target: str | None
    hits: list[SearchHit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "target": self.target,
            "hits": [hit.to_dict() for hit in self.hits],
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class QueryResponse:
    backend: str
    target: str | None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
