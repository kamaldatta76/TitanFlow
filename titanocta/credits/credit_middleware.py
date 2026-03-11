"""Credit debit middleware for managed tiers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from .credit_events import (
    EVENT_CREDIT_WARNING_80,
    EVENT_CREDIT_WARNING_95,
    EVENT_SOFT_CAP_ENGAGED,
    emit_credit_event,
)


@dataclass(frozen=True)
class CreditResult:
    user_id: str
    allowed: bool
    cost: float
    credit_used: float
    credit_limit_monthly: float
    usage_ratio: float
    warning_events: tuple[str, ...]
    soft_cap_engaged: bool
    soft_cap_strategy: str | None


class CreditMiddleware:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def debit_credit(self, user_id: str, cost: float) -> CreditResult:
        if cost < 0:
            raise ValueError("cost must be non-negative")

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        row = conn.execute(
            """
            select credit_used, credit_limit_monthly, warning_thresholds, soft_cap_strategy
            from octa_users where user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            conn.close()
            raise ValueError(f"Unknown TitanOcta user: {user_id}")

        before = float(row["credit_used"])
        limit = float(row["credit_limit_monthly"])
        after = before + float(cost)
        warning_thresholds = self._parse_thresholds(row["warning_thresholds"])
        soft_cap_strategy = row["soft_cap_strategy"]
        warning_events: list[str] = []

        if limit > 0.0:
            for threshold in warning_thresholds:
                crossed = before < (limit * threshold) <= after
                if not crossed:
                    continue
                event_type = self._threshold_event(threshold)
                if event_type:
                    warning_events.append(event_type)
                    emit_credit_event(
                        conn,
                        user_id=user_id,
                        event_type=event_type,
                        metadata={
                            "threshold": threshold,
                            "credit_used": after,
                            "credit_limit_monthly": limit,
                            "usage_ratio": after / limit if limit else 0.0,
                        },
                    )

        soft_cap_engaged = bool(limit > 0.0 and before < limit <= after)
        if soft_cap_engaged:
            emit_credit_event(
                conn,
                user_id=user_id,
                event_type=EVENT_SOFT_CAP_ENGAGED,
                metadata={
                    "credit_used": after,
                    "credit_limit_monthly": limit,
                    "usage_ratio": after / limit if limit else 0.0,
                    "soft_cap_strategy": soft_cap_strategy or "cheapest_allowed",
                },
            )

        conn.execute(
            "update octa_users set credit_used = ?, updated_at = CURRENT_TIMESTAMP where user_id = ?",
            (after, user_id),
        )
        conn.commit()
        conn.close()

        usage_ratio = (after / limit) if limit > 0 else 0.0
        return CreditResult(
            user_id=user_id,
            allowed=True,
            cost=float(cost),
            credit_used=after,
            credit_limit_monthly=limit,
            usage_ratio=usage_ratio,
            warning_events=tuple(warning_events),
            soft_cap_engaged=soft_cap_engaged,
            soft_cap_strategy=soft_cap_strategy,
        )

    @staticmethod
    def _parse_thresholds(value: Any) -> list[float]:
        if value is None:
            return [0.80, 0.95]
        if isinstance(value, str):
            import json

            try:
                raw = json.loads(value)
                if isinstance(raw, list):
                    return [float(x) for x in raw]
            except Exception:  # noqa: BLE001
                return [0.80, 0.95]
        if isinstance(value, list):
            return [float(x) for x in value]
        return [0.80, 0.95]

    @staticmethod
    def _threshold_event(threshold: float) -> str | None:
        if abs(threshold - 0.80) < 1e-6:
            return EVENT_CREDIT_WARNING_80
        if abs(threshold - 0.95) < 1e-6:
            return EVENT_CREDIT_WARNING_95
        return None

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists octa_users (
                user_id text primary key,
                credit_used real not null default 0.0,
                credit_limit_monthly real not null default 0.0,
                warning_thresholds text not null default '[0.8, 0.95]',
                soft_cap_strategy text
            )
            """
        )
        conn.execute(
            """
            create table if not exists octa_audit (
                id integer primary key autoincrement,
                user_id text not null,
                timestamp text not null,
                event_type text not null,
                metadata text not null
            )
            """
        )
