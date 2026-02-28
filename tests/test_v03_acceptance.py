"""v0.3 acceptance tests (scaffold)."""

from __future__ import annotations

import asyncio

import pytest

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.ipc_outbound_loop import IPCOutboundLoop
from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.session_manager import SessionManager


@pytest.mark.asyncio
async def test_outbound_ttl_drop(tmp_path):
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
    sessions = SessionManager(db, session_ttl_days=cfg.session_ttl_days)
    ipc = IPCServer(db=db, clock=clock, config=cfg, sessions=sessions)

    sent = []

    async def sender(env):
        sent.append(env.trace_id)

    loop = IPCOutboundLoop(ipc=ipc, clock=clock, sender=sender)

    env = IPCEnvelope(
        trace_id="t-ttl",
        session_id="sess",
        actor_id=cfg.allowed_actors[0],
        created_monotonic=clock.now() - 999,
        priority=0,
        module_id="core",
        method="test",
        payload={},
    )

    await ipc.accept_inbound(env)

    task = asyncio.create_task(loop.start("core"))
    await asyncio.sleep(0.1)
    await loop.stop()

    assert "t-ttl" not in sent

    await db.stop()
