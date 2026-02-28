"""Module outbound dispatch scaffold."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer


class ModuleDispatcher:
    def __init__(self, ipc: IPCServer, socket_path: str) -> None:
        self._ipc = ipc
        self._socket_path = socket_path
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
            await self._send(envelope)

    async def _send(self, envelope: IPCEnvelope) -> None:
        reader, writer = await asyncio.open_unix_connection(self._socket_path)
        writer.write(json.dumps(envelope.__dict__).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
