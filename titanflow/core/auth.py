"""Auth + manifest enforcement for v0.2 IPC."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Session:
    module_id: str
    session_id: str
    permissions: dict[str, Any]


class AuthManager:
    def __init__(self, manifest_dir: str) -> None:
        self.manifest_dir = Path(manifest_dir)
        self._manifests: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, Session] = {}

    def load_manifests(self) -> None:
        self._manifests.clear()
        if not self.manifest_dir.exists():
            return
        for path in self.manifest_dir.glob("*.yaml"):
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            module_id = data.get("module", {}).get("id")
            if module_id:
                self._manifests[module_id] = data

    def get_manifest(self, module_id: str) -> dict[str, Any] | None:
        return self._manifests.get(module_id)

    def list_manifests(self) -> dict[str, dict[str, Any]]:
        return dict(self._manifests)

    def validate_token(self, module_id: str, token: str) -> bool:
        manifest = self.get_manifest(module_id)
        if not manifest:
            return False
        token_file = manifest.get("module", {}).get("token_file")
        if not token_file:
            return False
        try:
            expected = Path(token_file).read_text().strip()
        except FileNotFoundError:
            return False
        return secrets.compare_digest(expected, token)

    def register_session(self, module_id: str) -> Session:
        session_id = secrets.token_hex(16)
        manifest = self.get_manifest(module_id) or {}
        perms = manifest.get("permissions", {})
        session = Session(module_id=module_id, session_id=session_id, permissions=perms)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
