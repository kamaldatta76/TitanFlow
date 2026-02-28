"""Bridge telemetry AF_UNIX to HTTP snapshot (scaffold)."""

from __future__ import annotations

import asyncio
import json
from typing import Any


async def fetch_unix_snapshot(socket_path: str) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    data = await reader.readline()
    writer.close()
    await writer.wait_closed()
    if not data:
        return {}
    return json.loads(data.decode())
