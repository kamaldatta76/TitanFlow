"""Telemetry snapshot for dashboards (v0.3 scaffold)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from titanflow.v03.db_broker import SQLiteBroker


@dataclass
class TelemetrySnapshot:
    db_state: str
    dlq_size: int
    metrics: dict[str, int]


async def collect_snapshot(db: SQLiteBroker) -> TelemetrySnapshot:
    def _dlq_count(conn):
        row = conn.execute("SELECT COUNT(*) FROM dead_letter").fetchone()
        return int(row[0]) if row else 0

    def _metrics(conn):
        rows = conn.execute("SELECT key, value FROM metrics_counters").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    dlq_size = await db.run(_dlq_count, trace_id="SYSTEM", module_id="core", method="telemetry.dlq")
    metrics = await db.run(_metrics, trace_id="SYSTEM", module_id="core", method="telemetry.metrics")

    return TelemetrySnapshot(
        db_state=db.state,
        dlq_size=dlq_size,
        metrics=metrics,
    )
