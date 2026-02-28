"""Trace ID helpers (ULID preferred, UUID fallback)."""

from __future__ import annotations

import uuid


def new_trace_id() -> str:
    try:
        import ulid  # type: ignore

        return str(ulid.new())
    except Exception:
        return str(uuid.uuid4())
