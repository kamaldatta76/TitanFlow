"""AF_UNIX telemetry server scaffold."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable

from titanflow.v03.telemetry import collect_snapshot
from titanflow.v03.db_broker import SQLiteBroker


class TelemetryServer:
    def __init__(self, socket_path: str, db: SQLiteBroker) -> None:
        self._socket_path = socket_path
        self._db = db
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        socket_path = Path(self._socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=self._socket_path)
        os.chmod(self._socket_path, 0o666)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        snapshot = await collect_snapshot(self._db)
        payload = {
            "db_state": snapshot.db_state,
            "dlq_size": snapshot.dlq_size,
            "metrics": snapshot.metrics,
        }
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
