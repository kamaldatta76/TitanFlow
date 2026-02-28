"""Session manager enforcing actor isolation."""

from __future__ import annotations

import json
from typing import Any

from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.kernel_clock import KernelClock


class SessionManager:
    def __init__(self, db: SQLiteBroker, *, session_ttl_days: int) -> None:
        self._db = db
        self._session_ttl_days = session_ttl_days

    async def validate_session(self, session_id: str, actor_id: str) -> bool:
        def _run(conn):
            row = conn.execute(
                "SELECT actor_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            return row[0] == actor_id

        return await self._db.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="sessions.validate",
        )

    async def touch_session(self, session_id: str, actor_id: str, metadata: dict[str, Any] | None = None) -> None:
        def _run(conn):
            conn.execute(
                "UPDATE sessions SET last_active = datetime('now'), metadata = ? WHERE session_id = ?",
                (json.dumps(metadata or {}), session_id),
            )

        await self._db.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="sessions.touch",
        )

    async def cleanup_sessions(self) -> None:
        def _run(conn):
            conn.execute(
                "DELETE FROM sessions WHERE last_active < datetime('now', ?)",
                (f"-{self._session_ttl_days} days",),
            )

        await self._db.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="sessions.cleanup",
        )
