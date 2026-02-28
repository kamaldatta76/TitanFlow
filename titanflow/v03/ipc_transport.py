"""IPC transport scaffold for AF_UNIX sockets."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.trace_id import new_trace_id


class IPCTransport:
    def __init__(self, socket_path: str, server: IPCServer) -> None:
        self._socket_path = socket_path
        self._server = server
        self._server_task: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server_task = await asyncio.start_unix_server(self._handle, path=self._socket_path)

    async def stop(self) -> None:
        if self._server_task:
            self._server_task.close()
            await self._server_task.wait_closed()
            self._server_task = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                payload = json.loads(line.decode())
                envelope = IPCEnvelope(**payload)
                await self._server.validate_session(envelope)
                await self._server.accept_inbound(envelope)
                writer.write(b"{\"status\":\"ok\"}\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
