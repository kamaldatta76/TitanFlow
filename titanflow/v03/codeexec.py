"""CodeExec scaffold enforcing per-actor workspace mount."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from titanflow.v03.workspace_manager import WorkspaceManager


@dataclass
class CodeExecRequest:
    actor_id: str
    code: str
    language: str


class CodeExec:
    def __init__(self, workspaces: WorkspaceManager) -> None:
        self._workspaces = workspaces

    def prepare(self, req: CodeExecRequest) -> dict[str, Any]:
        workspace = self._workspaces.ensure(req.actor_id)
        return {
            "actor_id": req.actor_id,
            "workspace": str(workspace),
            "language": req.language,
            "code": req.code,
        }
