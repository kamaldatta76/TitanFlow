"""IPC outbound loop with TTL checks and drop policy."""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.kernel_clock import KernelClock


class IPCOutboundLoop:
    TTL_BY_PRIORITY = {0: 5.0, 1: 30.0, 2: 300.0}

    def __init__(
        self,
        *,
        ipc: IPCServer,
        clock: KernelClock,
        sender: Callable[[IPCEnvelope], asyncio.Future],
    ) -> None:
        self._ipc = ipc
        self._clock = clock
        self._sender = sender
        self._task: asyncio.Task | None = None

    async def start(self, module_id: str) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(module_id))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self, module_id: str) -> None:
        while True:
            envelope = await self._ipc.next_inbound(module_id)
            ttl = self.TTL_BY_PRIORITY.get(envelope.priority, 30.0)
            age = self._clock.now() - envelope.created_monotonic
            if age > ttl:
                await self._ipc._drop(envelope, reason="ttl_expired", queue_name="outbound")
                await self._ipc._db.increment_counter(f"ttl_drop.module={module_id}")
                continue
            await self._sender(envelope)
