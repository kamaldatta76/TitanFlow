"""LLM broker preemption DLQ tests (scaffold)."""

from __future__ import annotations

import asyncio

import pytest

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.llm_broker import LLMBroker, LLMRequest


@pytest.mark.asyncio
async def test_llm_preemption_dlq(tmp_path):
    cfg = CoreConfig()
    db = SQLiteBroker(
        str(tmp_path / "v03.db"),
        max_queue=cfg.db_max_queue,
        enqueue_timeout_s=cfg.db_job_enqueue_timeout_s,
        exec_timeout_s=cfg.db_job_exec_timeout_s,
        wal_pressure_bytes=cfg.wal_pressure_bytes,
        shutdown_deadline_s=cfg.shutdown_deadline_s,
    )
    await db.start()
    await db.init_schema()

    clock = KernelClock()

    async def slow_stream(req: LLMRequest) -> str:
        await asyncio.sleep(10)
        return "ok"

    broker = LLMBroker(clock=clock, db=db, config=cfg, llm_stream_fn=slow_stream)
    await broker.start()

    req = LLMRequest(priority=2, created_monotonic=clock.now(), trace_id="t1", prompt="A")
    req.attempts = 4
    await broker._dlq(req, reason="max_preemptions_exceeded")

    def _count(conn):
        row = conn.execute(
            "SELECT COUNT(*) FROM dead_letter WHERE reason = ?",
            ("max_preemptions_exceeded",),
        ).fetchone()
        return int(row[0]) if row else 0

    count = await db.run(_count, trace_id="SYSTEM", module_id="core", method="dlq.count")
    assert count >= 1

    await db.stop()
