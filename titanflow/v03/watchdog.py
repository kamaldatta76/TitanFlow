"""systemd sd_notify watchdog (v0.3)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from typing import Awaitable, Callable

from titanflow.v03.kernel_clock import KernelClock


def _notify_socket_addr() -> str | None:
    return os.getenv("NOTIFY_SOCKET")


def _sd_notify(payload: str) -> None:
    addr = _notify_socket_addr()
    if not addr:
        return

    data = payload.encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        if addr.startswith("@"):
            sock.connect("\0" + addr[1:])
        else:
            sock.connect(addr)
        sock.sendall(data)
    finally:
        sock.close()


def _ensure_coroutine(fn: Callable[[], bool | Awaitable[bool]]) -> Awaitable[bool]:
    result = fn()
    if asyncio.iscoroutine(result):
        return result
    async def _wrap() -> bool:
        return bool(result)
    return _wrap()


class Watchdog:
    def __init__(
        self,
        *,
        clock: KernelClock,
        watchdog_sec: float,
        lag_max_s: float,
        health_check: Callable[[], bool | Awaitable[bool]],
    ) -> None:
        self._clock = clock
        self._interval = max(watchdog_sec / 2.0, 1.0)
        self._lag_max_s = lag_max_s
        self._health_check = health_check
        self._task: asyncio.Task | None = None
        self._enabled = _notify_socket_addr() is not None

    async def start(self) -> None:
        if not self._enabled:
            return
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def notify_ready(self) -> None:
        if not self._enabled:
            return
        _sd_notify("READY=1")

    async def _loop(self) -> None:
        expected = self._clock.now()
        while True:
            await asyncio.sleep(self._interval)
            now = self._clock.now()
            lag = now - expected - self._interval
            expected = now

            healthy = await _ensure_coroutine(self._health_check)
            if lag <= self._lag_max_s and healthy:
                _sd_notify("WATCHDOG=1")
