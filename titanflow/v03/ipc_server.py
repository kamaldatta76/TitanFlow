"""IPC routing scaffold with bounded queues, TTL, and DLQ drops."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.session_manager import SessionManager


class IPCValidationError(Exception):
    pass


@dataclass
class IPCEnvelope:
    trace_id: str
    session_id: str
    actor_id: str
    created_monotonic: float
    priority: int
    module_id: str
    method: str
    payload: dict[str, Any]
    stream: bool = False


@dataclass
class ModuleQueues:
    inbound: asyncio.Queue
    outbound: asyncio.Queue
    token_bucket: "TokenBucket"


class TokenBucket:
    def __init__(self, rate_per_min: int, clock: KernelClock) -> None:
        self._rate = rate_per_min
        self._tokens = rate_per_min
        self._last = clock.now()
        self._clock = clock

    def allow(self) -> bool:
        now = self._clock.now()
        elapsed = max(0.0, now - self._last)
        refill = (elapsed / 60.0) * self._rate
        self._tokens = min(self._rate, self._tokens + refill)
        self._last = now
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class IPCServer:
    TTL_BY_PRIORITY = {0: 5.0, 1: 30.0, 2: 300.0}

    def __init__(
        self,
        *,
        db: SQLiteBroker,
        clock: KernelClock,
        config: CoreConfig,
        sessions: SessionManager,
    ) -> None:
        self._db = db
        self._clock = clock
        self._config = config
        self._sessions = sessions
        self._modules: dict[str, ModuleQueues] = {}

    def register_module(self, module_id: str, rate_per_min: int = 60) -> None:
        if module_id in self._modules:
            return
        inbound = asyncio.Queue(maxsize=self._config.ipc_in_q_max)
        outbound = asyncio.Queue(maxsize=self._config.ipc_out_q_max)
        self._modules[module_id] = ModuleQueues(
            inbound=inbound,
            outbound=outbound,
            token_bucket=TokenBucket(rate_per_min, self._clock),
        )

    async def accept_inbound(self, envelope: IPCEnvelope) -> None:
        self._validate_envelope(envelope)
        queues = self._modules.get(envelope.module_id)
        if not queues:
            self.register_module(envelope.module_id)
            queues = self._modules[envelope.module_id]

        if not queues.token_bucket.allow():
            await self._drop(envelope, reason="rate_limited", queue_name="inbound")
            await self._db.increment_counter(f"rate_limited.module={envelope.module_id}")
            return

        if queues.inbound.full():
            await self._drop(envelope, reason="inbound_queue_full", queue_name="inbound")
            await self._db.increment_counter(f"drop_inbound_queue_full.module={envelope.module_id}")
            return

        await queues.inbound.put(envelope)

    async def next_inbound(self, module_id: str) -> IPCEnvelope:
        queues = self._modules[module_id]
        envelope: IPCEnvelope = await queues.inbound.get()
        ttl = self.TTL_BY_PRIORITY.get(envelope.priority, 30.0)
        age = self._clock.now() - envelope.created_monotonic
        if age > ttl:
            await self._drop(envelope, reason="ttl_expired", queue_name="inbound")
            await self._db.increment_counter(f"ttl_drop.module={module_id}")
            raise IPCValidationError("TTL expired")
        return envelope

    async def send_outbound(self, envelope: IPCEnvelope) -> None:
        queues = self._modules.get(envelope.module_id)
        if not queues:
            self.register_module(envelope.module_id)
            queues = self._modules[envelope.module_id]

        if envelope.stream:
            if queues.outbound.full():
                try:
                    queues.outbound.get_nowait()
                    queues.outbound.task_done()
                except asyncio.QueueEmpty:
                    pass
            await queues.outbound.put(envelope)
            return

        if queues.outbound.full():
            await self._drop(envelope, reason="outbound_queue_full", queue_name="outbound")
            await self._db.increment_counter(f"drop_outbound_queue_full.module={envelope.module_id}")
            return

        await queues.outbound.put(envelope)

    def _validate_envelope(self, envelope: IPCEnvelope) -> None:
        if not envelope.trace_id:
            raise IPCValidationError("missing trace_id")
        if not envelope.session_id or not envelope.actor_id:
            raise IPCValidationError("missing session_id/actor_id")
        if envelope.actor_id not in self._config.allowed_actors:
            raise IPCValidationError("actor not allowed")

    async def validate_session(self, envelope: IPCEnvelope) -> None:
        valid = await self._sessions.validate_session(envelope.session_id, envelope.actor_id)
        if not valid:
            raise IPCValidationError("invalid session")

    async def _drop(self, envelope: IPCEnvelope, *, reason: str, queue_name: str) -> None:
        age_ms = int((self._clock.now() - envelope.created_monotonic) * 1000)
        await self._db.insert_dead_letter(
            trace_id=envelope.trace_id,
            session_id=envelope.session_id,
            actor_id=envelope.actor_id,
            module_id=envelope.module_id,
            method=envelope.method,
            reason=reason,
            payload=envelope.payload,
            priority=envelope.priority,
            queue_name=queue_name,
            age_ms=age_ms,
        )
