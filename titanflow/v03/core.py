"""v0.3 core runner scaffold."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.ipc_server import IPCServer
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.scheduler import AsyncScheduler
from titanflow.v03.session_manager import SessionManager
from titanflow.v03.telemetry_server import TelemetryServer
from titanflow.v03.watchdog import Watchdog
from titanflow.v03.cache_manager import CacheManager
from titanflow.v03.llm_broker import LLMBroker, LLMRequest
from titanflow.v03.ipc_transport import IPCTransport
from titanflow.v03.module_dispatch import ModuleDispatcher

logger = logging.getLogger("titanflow.v03.core")


class Core:
    def __init__(self, *, config: CoreConfig, db_path: str) -> None:
        self._config = config
        self._clock = KernelClock()
        self._db = SQLiteBroker(
            db_path,
            max_queue=config.db_max_queue,
            enqueue_timeout_s=config.db_job_enqueue_timeout_s,
            exec_timeout_s=config.db_job_exec_timeout_s,
            wal_pressure_bytes=config.wal_pressure_bytes,
            shutdown_deadline_s=config.shutdown_deadline_s,
        )
        self._sessions = SessionManager(self._db, session_ttl_days=config.session_ttl_days)
        self._ipc = IPCServer(db=self._db, clock=self._clock, config=config, sessions=self._sessions)
        self._scheduler = AsyncScheduler(self._clock)
        self._telemetry = TelemetryServer(config.telemetry_socket, self._db)
        core_socket = config.core_socket if hasattr(config, "core_socket") else "/run/titanflow/core.sock"
        self._ipc_transport = IPCTransport(core_socket, self._ipc)
        self._dispatcher = ModuleDispatcher(self._ipc, core_socket)
        # LLM broker wiring placeholder: llm_stream_fn injected by caller later
        self._llm: LLMBroker | None = None
        self._cache: CacheManager | None = None
        self._watchdog = Watchdog(
            clock=self._clock,
            watchdog_sec=config.watchdog_sec,
            lag_max_s=config.watchdog_lag_max_s,
            health_check=self._health_check,
        )

    async def start(self) -> None:
        await self._db.start()
        await self._db.init_schema()
        await self._telemetry.start()
        await self._ipc_transport.start()
        self._scheduler.every(self._config.wal_passive_every_s, self._db.checkpoint_passive)
        self._scheduler.every(self._config.wal_truncate_every_s, self._db.checkpoint_truncate)
        self._scheduler.every(3600, self._evict_cache)
        self._scheduler.every(3600, self._sessions.cleanup_sessions)
        await self._dispatcher.start("core")
        await self._watchdog.start()
        self._watchdog.notify_ready()
        logger.info("v0.3 Core started")

    async def stop(self) -> None:
        await self._watchdog.stop()
        await self._dispatcher.stop()
        await self._ipc_transport.stop()
        await self._telemetry.stop()
        await self._scheduler.stop()
        await self._db.stop()
        logger.info("v0.3 Core stopped")

    def attach_llm(self, broker: LLMBroker) -> None:
        self._llm = broker
        self._cache = CacheManager(broker)

    async def _evict_cache(self) -> None:
        if self._cache is None:
            return
        await self._cache.evict()

    async def _health_check(self) -> bool:
        return self._db.is_running

    @property
    def ipc(self) -> IPCServer:
        return self._ipc

    @property
    def db(self) -> SQLiteBroker:
        return self._db
