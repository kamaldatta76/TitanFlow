"""IPC inbound processing loop scaffold."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer


class IPCInboundLoop:
    def __init__(
        self,
        *,
        ipc: IPCServer,
        handler: Callable[[IPCEnvelope], Awaitable[None]],
    ) -> None:
        self._ipc = ipc
        self._handler = handler
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
            await self._handler(envelope)
