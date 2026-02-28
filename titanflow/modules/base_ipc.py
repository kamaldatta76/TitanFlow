"""IPC client base class for modules."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("titanflow.module_ipc")


class ModuleBaseIPC:
    def __init__(self) -> None:
        self.module_id = os.environ.get("TITANFLOW_MODULE_ID", "research")
        self.core_socket = os.environ.get("TITANFLOW_CORE_SOCKET", "/run/titanflow/core.sock")
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._counter = 0
        self.session_id: str | None = None
        self._heartbeat_interval = int(os.environ.get("TITANFLOW_HEARTBEAT_INTERVAL", "60"))
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.core_socket)
        token_path = os.environ.get("TITANFLOW_MODULE_TOKEN", f"/etc/titanflow/secrets/{self.module_id}.token")
        token = Path(token_path).read_text().strip()

        response = await self._rpc("auth.register", {"version": self.version}, token=token)
        if response.get("status") != "ok":
            raise RuntimeError(f"Auth failed: {response}")
        self.session_id = response["result"]["session_id"]
        logger.info("Authenticated with Core. Session ID: %s", self.session_id)
        asyncio.create_task(self._listen())
        if self._heartbeat_interval > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self.run()

    async def _listen(self) -> None:
        assert self._reader is not None
        while True:
            line = await self._reader.readline()
            if not line:
                logger.error("Core connection closed")
                break
            msg = json.loads(line.decode())
            req_id = msg.get("id")
            fut = self._pending.pop(req_id, None)
            if fut:
                fut.set_result(msg)

    async def _rpc(self, method: str, params: dict, token: str | None = None) -> dict:
        self._counter += 1
        req_id = f"{self.module_id}-{self._counter:06d}"
        payload = {
            "id": req_id,
            "module": self.module_id,
            "method": method,
            "params": params,
        }
        if token:
            payload["token"] = token
        else:
            payload["session_id"] = self.session_id

        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        assert self._writer is not None
        self._writer.write((json.dumps(payload) + "\n").encode())
        await self._writer.drain()
        return await fut

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self._rpc("health.pong", {})
            except Exception as exc:
                logger.debug("Heartbeat failed: %s", exc)
            await asyncio.sleep(self._heartbeat_interval)

    async def llm_generate(self, prompt: str, model: str | None = None, max_tokens: int = 1024) -> str:
        resp = await self._rpc("llm.generate", {
            "prompt": prompt,
            "model": model,
            "max_tokens": max_tokens,
        })
        if resp.get("status") != "ok":
            raise RuntimeError(resp)
        return resp["result"]["text"]

    async def db_query(self, table: str, query: str, params: list | None = None) -> list[dict]:
        resp = await self._rpc("db.query", {
            "table": table,
            "query": query,
            "params": params or [],
        })
        if resp.get("status") != "ok":
            raise RuntimeError(resp)
        return resp["result"]["rows"]

    async def db_insert(self, table: str, data: dict) -> int:
        resp = await self._rpc("db.insert", {
            "table": table,
            "data": data,
        })
        if resp.get("status") != "ok":
            raise RuntimeError(resp)
        return resp["result"]["row_id"]

    async def db_update(self, table: str, data: dict, where: str, params: list | None = None) -> int:
        resp = await self._rpc("db.update", {
            "table": table,
            "data": data,
            "where": where,
            "params": params or [],
        })
        if resp.get("status") != "ok":
            raise RuntimeError(resp)
        return resp["result"]["updated"]

    async def http_request(self, url: str, method: str = "GET", headers: dict | None = None, body: str | None = None) -> dict:
        resp = await self._rpc("http.request", {
            "url": url,
            "method": method,
            "headers": headers or {},
            "body": body,
        })
        if resp.get("status") != "ok":
            raise RuntimeError(resp)
        return resp["result"]

    @property
    def version(self) -> str:
        return "0.2.0"

    async def run(self) -> None:
        raise NotImplementedError
