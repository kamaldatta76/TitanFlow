"""Audit event helpers for TitanOcta credit/routing events."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

EVENT_CREDIT_WARNING_80 = "credit_warning_80"
EVENT_CREDIT_WARNING_95 = "credit_warning_95"
EVENT_SOFT_CAP_ENGAGED = "soft_cap_engaged"
EVENT_PROVIDER_EXCLUDED_ROUTE_BLOCKED = "provider_excluded_route_blocked"

_SENSITIVE_KEYS = {"api_key", "apikey", "token", "secret", "password", "key_value"}


def emit_credit_event(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    payload = _sanitize_metadata(metadata)
    conn.execute(
        "insert into octa_audit (user_id, timestamp, event_type, metadata) values (?, ?, ?, ?)",
        (user_id, _utc_now(), event_type, json.dumps(payload, sort_keys=True)),
    )


def _sanitize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        k = str(key)
        if k.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            clean[k] = _sanitize_metadata(value)
        else:
            clean[k] = value
    return clean


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
