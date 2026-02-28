"""WAL pressure checkpoint tests (scaffold)."""

from __future__ import annotations

import os
import tempfile

import pytest

from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.config import CoreConfig


@pytest.mark.asyncio
async def test_wal_pressure_trigger(tmp_path, monkeypatch):
    db_path = tmp_path / "v03.db"
    cfg = CoreConfig()
    db = SQLiteBroker(
        str(db_path),
        max_queue=cfg.db_max_queue,
        enqueue_timeout_s=cfg.db_job_enqueue_timeout_s,
        exec_timeout_s=cfg.db_job_exec_timeout_s,
        wal_pressure_bytes=1,  # force trigger
        shutdown_deadline_s=cfg.shutdown_deadline_s,
    )
    await db.start()
    await db.init_schema()

    # Create a WAL file to trigger pressure check
    wal_path = str(db_path) + "-wal"
    with open(wal_path, "wb") as f:
        f.write(b"0" * 10)

    triggered = {"hit": False}
    original = db._checkpoint_if_needed

    def _patched(conn):
        triggered["hit"] = True
        return original(conn)

    db._checkpoint_if_needed = _patched  # type: ignore[assignment]

    # Run a trivial job to trigger checkpoint check
    await db.run(lambda conn: 1, trace_id="SYSTEM", module_id="core", method="noop")

    assert triggered["hit"] is True
    await db.stop()
