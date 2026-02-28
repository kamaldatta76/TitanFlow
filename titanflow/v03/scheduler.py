"""Async scheduler helpers for periodic jobs (v0.3 scaffold)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, Callable

from titanflow.v03.kernel_clock import KernelClock


class AsyncScheduler:
    def __init__(self, clock: KernelClock) -> None:
        self._clock = clock
        self._tasks: list[asyncio.Task] = []

    def every(self, interval_s: float, coro_fn: Callable[[], Awaitable[None]]) -> None:
        async def _runner():
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await coro_fn()
                except Exception:
                    # Intentionally swallow to keep scheduler alive
                    pass

        self._tasks.append(asyncio.create_task(_runner()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
