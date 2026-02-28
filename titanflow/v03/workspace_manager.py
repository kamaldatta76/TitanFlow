"""Workspace manager enforcing actor isolation."""

from __future__ import annotations

from pathlib import Path


class WorkspaceManager:
    def __init__(self, root: str = "/var/lib/titanflow/workspaces") -> None:
        self._root = Path(root)

    def resolve(self, actor_id: str) -> Path:
        if not actor_id or "/" in actor_id or ".." in actor_id:
            raise ValueError("invalid actor_id")
        path = self._root / actor_id
        return path

    def ensure(self, actor_id: str) -> Path:
        path = self.resolve(actor_id)
        path.mkdir(parents=True, exist_ok=True)
        return path
