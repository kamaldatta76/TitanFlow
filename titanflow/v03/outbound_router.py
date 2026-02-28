"""Outbound IPC router scaffold."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Callable

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer


class OutboundRouter:
    def __init__(self, ipc: IPCServer, sender: Callable[[IPCEnvelope], asyncio.Future]) -> None:
        self._ipc = ipc
        self._sender = sender
        self._task: asyncio.Task | None = None

    async def start(self, module_id: str) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(module_id))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self, module_id: str) -> None:
        while True:
            envelope = await self._ipc.next_inbound(module_id)
            await self._sender(envelope)
