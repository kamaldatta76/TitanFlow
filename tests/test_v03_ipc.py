"""v0.3 IPC TTL and drop policy tests (scaffold)."""

from __future__ import annotations

import asyncio

import pytest

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.session_manager import SessionManager


@pytest.mark.asyncio
async def test_ipc_ttl_drop(tmp_path):
    db_path = tmp_path / "v03.db"
    cfg = CoreConfig()
    db = SQLiteBroker(
        str(db_path),
        max_queue=cfg.db_max_queue,
        enqueue_timeout_s=cfg.db_job_enqueue_timeout_s,
        exec_timeout_s=cfg.db_job_exec_timeout_s,
        wal_pressure_bytes=cfg.wal_pressure_bytes,
        shutdown_deadline_s=cfg.shutdown_deadline_s,
    )
    await db.start()
    await db.init_schema()

    clock = KernelClock()
    sessions = SessionManager(db, session_ttl_days=cfg.session_ttl_days)
    ipc = IPCServer(db=db, clock=clock, config=cfg, sessions=sessions)

    envelope = IPCEnvelope(
        trace_id="trace-1",
        session_id="sess-1",
        actor_id=cfg.allowed_actors[0],
        created_monotonic=clock.now() - 999,
        priority=0,
        module_id="research",
        method="test",
        payload={},
    )

    await ipc.accept_inbound(envelope)

    with pytest.raises(Exception):
        await ipc.next_inbound("research")

    await db.stop()
