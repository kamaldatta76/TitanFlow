"""Logging helpers with trace context."""

from __future__ import annotations

import json
import logging
from typing import Any


class TraceAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]):
        extra = kwargs.get("extra", {})
        merged = {**self.extra, **extra}
        kwargs["extra"] = merged
        return msg, kwargs


def bind_logger(
    base: logging.Logger,
    *,
    trace_id: str,
    session_id: str | None,
    actor_id: str | None,
    module_id: str,
) -> TraceAdapter:
    return TraceAdapter(
        base,
        {
            "trace_id": trace_id,
            "session_id": session_id,
            "actor_id": actor_id,
            "module_id": module_id,
        },
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "session_id": getattr(record, "session_id", None),
            "actor_id": getattr(record, "actor_id", None),
            "module_id": getattr(record, "module_id", None),
        }
        return json.dumps(payload, ensure_ascii=False)
