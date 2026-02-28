"""LLM broker with preemption, cache, and DLQ routing (v0.3 scaffold)."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.kernel_clock import KernelClock


class LLMError(Exception):
    pass


@dataclass(order=True)
class LLMRequest:
    priority: int
    created_monotonic: float = field(compare=True)
    trace_id: str = field(compare=False, default="")
    session_id: str | None = field(compare=False, default=None)
    actor_id: str | None = field(compare=False, default=None)
    module_id: str = field(compare=False, default="core")
    attempts: int = field(compare=False, default=0)
    prompt: str = field(compare=False, default="")
    system_prompt: str = field(compare=False, default="")
    system_prompt_version: str = field(compare=False, default="v1")
    model: str = field(compare=False, default="")
    future: asyncio.Future = field(compare=False, default_factory=asyncio.Future)


class LLMBroker:
    def __init__(
        self,
        *,
        clock: KernelClock,
        db: SQLiteBroker,
        config: CoreConfig,
        llm_stream_fn: Callable[[LLMRequest], Awaitable[str]],
    ) -> None:
        self._clock = clock
        self._db = db
        self._config = config
        self._queue: asyncio.PriorityQueue[LLMRequest] = asyncio.PriorityQueue()
        self._worker_task: asyncio.Task | None = None
        self._active_task: asyncio.Task | None = None
        self._llm_stream_fn = llm_stream_fn

    async def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())

    async def submit(self, req: LLMRequest) -> str:
        await self._queue.put(req)
        return await req.future

    async def _worker(self) -> None:
        while True:
            req = await self._queue.get()
            # Preemption: CHAT (0) can cancel active RESEARCH (2)
            if req.priority == 0 and self._active_task and not self._active_task.done():
                self._active_task.cancel()

            self._active_task = asyncio.create_task(self._handle_request(req))
            try:
                await self._active_task
            except asyncio.CancelledError:
                req.attempts += 1
                if req.attempts > 3:
                    await self._dlq(req, reason="max_preemptions_exceeded")
                else:
                    await self._queue.put(req)
            finally:
                self._queue.task_done()

    async def _handle_request(self, req: LLMRequest) -> None:
        cache_key = self._cache_key(req)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            req.future.set_result(cached)
            return

        try:
            result = await self._llm_stream_fn(req)
        except Exception as exc:
            req.future.set_exception(exc)
            raise

        if len(result.encode("utf-8")) <= self._config.cache_max_bytes:
            await self._cache_put(req, cache_key, result)

        req.future.set_result(result)

    def _cache_key(self, req: LLMRequest) -> str:
        raw = f"{req.model}|{req.system_prompt_version}|{req.system_prompt}|{req.prompt}".encode()
        return hashlib.sha256(raw).hexdigest()

    async def _cache_get(self, cache_key: str) -> str | None:
        def _run(conn):
            row = conn.execute(
                "SELECT value FROM llm_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE llm_cache SET last_accessed = datetime('now') WHERE cache_key = ?",
                (cache_key,),
            )
            return row[0]

        return await self._db.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="cache.get",
        )

    async def _cache_put(self, req: LLMRequest, cache_key: str, value: str) -> None:
        value_bytes = len(value.encode("utf-8"))
        def _run(conn):
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, model, system_prompt_version, value, value_bytes)"
                " VALUES (?, ?, ?, ?, ?)",
                (cache_key, req.model, req.system_prompt_version, value, value_bytes),
            )
        await self._db.run(
            _run,
            trace_id=req.trace_id,
            session_id=req.session_id,
            actor_id=req.actor_id,
            module_id=req.module_id,
            method="cache.put",
            priority=req.priority,
        )

    async def evict_cache(self) -> None:
        ttl_days = self._config.cache_ttl_days
        max_rows = self._config.cache_max_rows
        def _run(conn):
            conn.execute(
                "DELETE FROM llm_cache WHERE created_at < datetime('now', ?)",
                (f"-{ttl_days} days",),
            )
            conn.execute(
                "DELETE FROM llm_cache WHERE cache_key NOT IN ("
                "SELECT cache_key FROM llm_cache ORDER BY last_accessed DESC LIMIT ?)",
                (max_rows,),
            )
        await self._db.run(
            _run,
            trace_id="SYSTEM",
            module_id="core",
            method="cache.evict",
        )

    async def _dlq(self, req: LLMRequest, *, reason: str) -> None:
        await self._db.insert_dead_letter(
            trace_id=req.trace_id,
            session_id=req.session_id,
            actor_id=req.actor_id,
            module_id=req.module_id,
            method="llm.request",
            reason=reason,
            payload={
                "model": req.model,
                "prompt": req.prompt[:2000],
                "system_prompt_version": req.system_prompt_version,
            },
            priority=req.priority,
            queue_name="llm",
            age_ms=int((self._clock.now() - req.created_monotonic) * 1000),
        )
