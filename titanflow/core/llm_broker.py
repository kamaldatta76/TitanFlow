"""LLM broker with priority queue."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from titanflow.core.llm import LLMClient

logger = logging.getLogger("titanflow.llm_broker")


class Priority(IntEnum):
    CHAT = 0
    MODULE = 1
    RESEARCH = 2


@dataclass(order=True)
class LLMRequest:
    priority: int
    timestamp: float = field(compare=True)
    kind: str = field(compare=False)  # "chat" or "generate"
    payload: dict[str, Any] = field(compare=False)
    future: asyncio.Future = field(compare=False)


class LLMBroker:
    def __init__(self, client: LLMClient, semaphore_limit: int = 1) -> None:
        self._client = client
        self._queue: asyncio.PriorityQueue[LLMRequest] = asyncio.PriorityQueue()
        self._semaphore = asyncio.Semaphore(semaphore_limit)
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        priority: Priority = Priority.MODULE,
    ) -> str:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        req = LLMRequest(
            priority=int(priority),
            timestamp=time.time(),
            kind="generate",
            payload={
                "prompt": prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            future=future,
        )
        await self._queue.put(req)
        return await future

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        priority: Priority = Priority.CHAT,
    ) -> str:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        req = LLMRequest(
            priority=int(priority),
            timestamp=time.time(),
            kind="chat",
            payload={
                "messages": messages,
                "model": model,
                "temperature": temperature,
            },
            future=future,
        )
        await self._queue.put(req)
        return await future

    async def _worker(self) -> None:
        while True:
            req = await self._queue.get()
            async with self._semaphore:
                try:
                    if req.kind == "generate":
                        result = await self._client.generate(**req.payload)
                    else:
                        result = await self._client.chat(**req.payload)
                    req.future.set_result(result)
                except Exception as exc:
                    req.future.set_exception(exc)
