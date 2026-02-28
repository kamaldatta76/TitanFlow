"""Module supervisor (MVP: detect disconnect and alert Papa)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

logger = logging.getLogger("titanflow.supervisor")


@dataclass
class ModuleState:
    module_id: str
    last_seen: float
    connected: bool = True
    alert_sent: bool = False


class ModuleSupervisor:
    def __init__(self, notify_fn: Callable[[str], Awaitable[None]], health_interval: int = 60) -> None:
        self._modules: dict[str, ModuleState] = {}
        self._notify = notify_fn
        self._health_interval = health_interval
        self._task: asyncio.Task | None = None

    def module_connected(self, module_id: str) -> None:
        self._modules[module_id] = ModuleState(
            module_id=module_id,
            last_seen=time.time(),
            connected=True,
            alert_sent=False,
        )
        logger.info("Module connected: %s", module_id)

    def module_heartbeat(self, module_id: str) -> None:
        if module_id in self._modules:
            self._modules[module_id].last_seen = time.time()

    async def module_disconnected(self, module_id: str) -> None:
        state = self._modules.get(module_id)
        if state and not state.connected and state.alert_sent:
            return
        if state:
            state.connected = False
            if state.alert_sent:
                return
            state.alert_sent = True
        logger.warning("Module disconnected: %s", module_id)
        await self._notify(f"⚠ TitanFlow module '{module_id}' disconnected.")

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watchdog())

    async def _watchdog(self) -> None:
        while True:
            now = time.time()
            for module_id, state in list(self._modules.items()):
                if state.connected and now - state.last_seen > self._health_interval * 3:
                    await self.module_disconnected(module_id)
            await asyncio.sleep(self._health_interval)

    def status(self) -> dict[str, dict]:
        return {
            module_id: {
                "connected": state.connected,
                "last_seen": state.last_seen,
            }
            for module_id, state in self._modules.items()
        }
