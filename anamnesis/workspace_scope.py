from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def normalize_workspace_root(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def workspace_root_text(path: str | Path) -> str:
    return str(normalize_workspace_root(path))


def workspace_root_aliases(path: str | Path) -> tuple[str, ...]:
    root_text = workspace_root_text(path)
    aliases = {root_text}
    if root_text.startswith("/private/"):
        aliases.add(root_text.removeprefix("/private"))
    elif root_text.startswith("/var/"):
        aliases.add(f"/private{root_text}")
    return tuple(sorted(aliases))


def workspace_contains_path(candidate: str | Path | None, workspace_root: str | Path) -> bool:
    if candidate in (None, ""):
        return False
    root = normalize_workspace_root(workspace_root)
    candidate_text = str(candidate).strip()
    if not candidate_text:
        return False
    for alias in workspace_root_aliases(root):
        if alias in candidate_text:
            return True
    try:
        resolved = Path(candidate_text).expanduser().resolve(strict=False)
    except Exception:
        return False
    for alias in workspace_root_aliases(root):
        alias_path = Path(alias).expanduser()
        try:
            resolved.relative_to(alias_path)
            return True
        except ValueError:
            if resolved == alias_path:
                return True
    return False


def iter_text_fragments(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from iter_text_fragments(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from iter_text_fragments(nested)


def payload_mentions_workspace(payload: Any, workspace_root: str | Path) -> bool:
    root = normalize_workspace_root(workspace_root)
    for fragment in iter_text_fragments(payload):
        if not fragment:
            continue
        for alias in workspace_root_aliases(root):
            if alias in fragment:
                return True
        if workspace_contains_path(fragment, root):
            return True
    return False


def apply_project_id(
    payload: dict[str, Any],
    project_id: str | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    item = dict(payload)
    if project_id and (
        force
        or not any(item.get(key) for key in ("project_id", "projectId", "cwd", "project"))
    ):
        item["project_id"] = project_id
    return item
