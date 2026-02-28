"""Audit logger for IPC events."""

from __future__ import annotations

import json
import logging
from typing import Any

from titanflow.core.database_broker import DatabaseBroker

logger = logging.getLogger("titanflow.audit")


class AuditLogger:
    def __init__(self, db: DatabaseBroker) -> None:
        self.db = db

    async def log(
        self,
        event_type: str,
        module_id: str = "core",
        method: str = "",
        status: str = "ok",
        details: dict[str, Any] | None = None,
        duration_ms: int = 0,
    ) -> None:
        try:
            payload = {
                "event_type": event_type,
                "user_id": None,
                "command": method,
                "args": module_id,
                "result": status,
                "details": json.dumps(details or {})[:1000],
                "duration_ms": duration_ms,
            }
            await self.db.insert("audit_log", payload)
        except Exception as exc:
            logger.debug("Audit log write failed: %s", exc, exc_info=True)
