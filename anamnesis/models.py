from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AgentKind = Literal["claude", "codex", "opencode"]
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
class CanonicalEvent:
    id: str
    agent: AgentKind
    session_id: str
    project_id: str
    ts: str
    kind: EventKind
    role: str | None = None
    content: str | None = None
    tool_name: str | None = None
    target_path: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RelationSchema:
    name: str
    kind: str
    columns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SchemaSummary:
    cell_path: str
    relations: list[RelationSchema] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    presets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_path": self.cell_path,
            "relations": [relation.to_dict() for relation in self.relations],
            "metadata": self.metadata,
            "presets": list(self.presets),
        }


@dataclass(slots=True)
class SearchHit:
    id: str
    score: float | None = None
    session_id: str | None = None
    project: str | None = None
    created_at: str | None = None
    ts: str | None = None
    type: str | None = None
    kind: str | None = None
    tool_name: str | None = None
    path: str | None = None
    target_path: str | None = None
    content: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ts is None and self.created_at is not None:
            self.ts = self.created_at
        if self.kind is None and self.type is not None:
            self.kind = self.type
        if self.target_path is None and self.path is not None:
            self.target_path = self.path

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FileTouchRecord:
    session_id: str
    ts: str
    kind: str
    path: str
    operation: str
    content: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionRecord:
    session_id: str
    first_seen_at: str
    last_seen_at: str
    event_count: int
    excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
