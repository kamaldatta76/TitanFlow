"""v0.3 SQLite broker: single-writer thread with backpressure + WAL control."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable

from titanflow.v03.kernel_clock import KernelClock

logger = logging.getLogger("titanflow.v03.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dead_letter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL,
  session_id TEXT,
  actor_id TEXT,
  module_id TEXT,
  method TEXT,
  queue TEXT,
  age_ms INTEGER,
  priority INTEGER DEFAULT 1,
  reason TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_monotonic REAL NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dead_letter_created ON dead_letter(created_at);
CREATE INDEX IF NOT EXISTS idx_dead_letter_trace ON dead_letter(trace_id);

CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  system_prompt_version TEXT NOT NULL,
  value TEXT NOT NULL,
  value_bytes INTEGER NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  last_accessed TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_last ON llm_cache(last_accessed);
CREATE INDEX IF NOT EXISTS idx_llm_cache_model ON llm_cache(model);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  last_active TEXT DEFAULT (datetime('now')),
  metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_actor ON sessions(actor_id);

CREATE TABLE IF NOT EXISTS metrics_counters (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  updated_at TEXT DEFAULT (datetime('now'))
);
"""


@dataclass
class DBJob:
    future: asyncio.Future
    fn: Callable[[sqlite3.Connection], Any]
    trace_id: str
    session_id: str | None
    actor_id: str | None
    module_id: str
    method: str
    priority: int
    created_monotonic: float
    timeout_handle: asyncio.Handle | None = None


class SQLiteBroker:
    """Single-writer SQLite broker running on a dedicated thread."""

    def __init__(
        self,
        db_path: str,
        *,
        max_queue: int,
        enqueue_timeout_s: float,
        exec_timeout_s: float,
        wal_pressure_bytes: int,
        shutdown_deadline_s: float,
    ) -> None:
        self.db_path = db_path
        self._q: queue.Queue[DBJob] = queue.Queue(maxsize=max_queue)
        self._enqueue_timeout_s = enqueue_timeout_s
        self._exec_timeout_s = exec_timeout_s
        self._wal_pressure_bytes = wal_pressure_bytes
        self._shutdown_deadline_s = shutdown_deadline_s

        self._accepting = True
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._state = "STARTING"
        self._ready = threading.Event()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == "RUNNING"

    async def start(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._thread_main, name="tf-db-broker", daemon=True)
        self._thread.start()
        await asyncio.to_thread(self._ready.wait, self._shutdown_deadline_s)

    async def stop(self) -> None:
        if self._state in {"DRAINING", "STOPPED"}:
            return
        self._state = "DRAINING"
        self._accepting = False
        self._stop.set()

        # poison pending jobs
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                break
            self._poison_job(job, asyncio.CancelledError("SQLiteBroker shutting down"))
            self._q.task_done()

        if self._thread:
            self._thread.join(timeout=self._shutdown_deadline_s)
            if self._thread.is_alive():
                logger.warning("SQLiteBroker thread did not stop before deadline")

        self._state = "STOPPED"

    async def run(
        self,
        fn: Callable[[sqlite3.Connection], Any],
        *,
        trace_id: str,
        session_id: str | None = None,
        actor_id: str | None = None,
        module_id: str = "core",
        method: str = "db.run",
        priority: int = 1,
    ) -> Any:
        if not self._accepting:
            raise RuntimeError("SQLiteBroker is draining/stopped")
        if self._loop is None:
            raise RuntimeError("SQLiteBroker not started")

        future: asyncio.Future = self._loop.create_future()
        job = DBJob(
            future=future,
            fn=fn,
            trace_id=trace_id,
            session_id=session_id,
            actor_id=actor_id,
            module_id=module_id,
            method=method,
            priority=priority,
            created_monotonic=KernelClock.now(),
        )

        # schedule exec timeout
        if self._exec_timeout_s > 0:
            job.timeout_handle = self._loop.call_later(
                self._exec_timeout_s, self._on_exec_timeout, job
            )

        # apply backpressure without blocking the loop
        await asyncio.to_thread(self._q.put, job, True, self._enqueue_timeout_s)
        return await future

    def _on_exec_timeout(self, job: DBJob) -> None:
        if job.future.done():
            return
        job.future.set_exception(asyncio.TimeoutError("DB job exec timeout"))
        # record timeout asynchronously
        if self._loop:
            self._loop.create_task(
                self.insert_dead_letter(
                    trace_id=job.trace_id,
                    session_id=job.session_id,
                    actor_id=job.actor_id,
                    module_id=job.module_id,
                    method=job.method,
                    reason="db_exec_timeout",
                    payload={"method": job.method},
                    priority=job.priority,
                    queue_name="db_broker",
                    age_ms=int((KernelClock.now() - job.created_monotonic) * 1000),
                )
            )
            self._loop.create_task(
                self.increment_counter(f"db_exec_timeout.module={job.module_id}")
            )

    def _poison_job(self, job: DBJob, exc: Exception) -> None:
        if job.timeout_handle:
            job.timeout_handle.cancel()
        if not job.future.done():
            self._loop.call_soon_threadsafe(job.future.set_exception, exc)

    def _resolve_job(self, job: DBJob, result: Any) -> None:
        if job.timeout_handle:
            job.timeout_handle.cancel()
        if not job.future.done():
            job.future.set_result(result)

    def _reject_job(self, job: DBJob, exc: Exception) -> None:
        if job.timeout_handle:
            job.timeout_handle.cancel()
        if not job.future.done():
            job.future.set_exception(exc)

    def _thread_main(self) -> None:
        conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(_SCHEMA_SQL)

        self._state = "RUNNING"
        self._ready.set()

        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=1.0)
            except queue.Empty:
                self._checkpoint_if_needed(conn)
                continue

            try:
                result = job.fn(conn)
                if self._loop:
                    self._loop.call_soon_threadsafe(self._resolve_job, job, result)
            except Exception as exc:
                if self._loop:
                    self._loop.call_soon_threadsafe(self._reject_job, job, exc)
            finally:
                self._q.task_done()
                self._checkpoint_if_needed(conn)

        conn.close()

    def _checkpoint_if_needed(self, conn: sqlite3.Connection) -> None:
        wal_path = f"{self.db_path}-wal"
        try:
            if os.path.exists(wal_path) and os.path.getsize(wal_path) > self._wal_pressure_bytes:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception as exc:
            logger.debug("WAL pressure check failed: %s", exc)

    async def init_schema(self, trace_id: str = "SYSTEM") -> None:
        await self.run(
            lambda conn: conn.executescript(_SCHEMA_SQL),
            trace_id=trace_id,
            module_id="core",
            method="db.init_schema",
        )

    async def increment_counter(self, key: str, delta: int = 1) -> None:
        def _run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO metrics_counters (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + ?, updated_at = datetime('now')",
                (key, delta, delta),
            )

        await self.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="metrics.increment",
        )

    async def insert_dead_letter(
        self,
        *,
        trace_id: str,
        session_id: str | None,
        actor_id: str | None,
        module_id: str,
        method: str,
        reason: str,
        payload: dict[str, Any],
        priority: int,
        queue_name: str,
        age_ms: int,
    ) -> None:
        def _run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO dead_letter (trace_id, session_id, actor_id, module_id, method, queue, age_ms, "
                "priority, reason, payload, created_monotonic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    session_id,
                    actor_id,
                    module_id,
                    method,
                    queue_name,
                    age_ms,
                    priority,
                    reason,
                    json.dumps(payload),
                    KernelClock.now(),
                ),
            )

        await self.run(
            _run,
            trace_id=trace_id,
            session_id=session_id,
            actor_id=actor_id,
            module_id=module_id,
            method=method,
            priority=priority,
        )

    async def checkpoint_passive(self, trace_id: str = "SYSTEM") -> None:
        await self.run(
            lambda conn: conn.execute("PRAGMA wal_checkpoint(PASSIVE)"),
            trace_id=trace_id,
            module_id="core",
            method="db.checkpoint.passive",
        )

    async def checkpoint_truncate(self, trace_id: str = "SYSTEM") -> None:
        await self.run(
            lambda conn: conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"),
            trace_id=trace_id,
            module_id="core",
            method="db.checkpoint.truncate",
        )
