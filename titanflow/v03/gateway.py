"""Gateway scaffold enforcing session + trace + actor binding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from titanflow.v03.config import CoreConfig
from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.session_manager import SessionManager
from titanflow.v03.trace_id import new_trace_id


@dataclass
class GatewayContext:
    session_id: str
    actor_id: str


class Gateway:
    def __init__(
        self,
        *,
        config: CoreConfig,
        clock: KernelClock,
        ipc: IPCServer,
        sessions: SessionManager,
    ) -> None:
        self._config = config
        self._clock = clock
        self._ipc = ipc
        self._sessions = sessions

    async def handle_request(
        self,
        *,
        session_id: str,
        actor_id: str,
        module_id: str,
        method: str,
        payload: dict[str, Any],
        priority: int,
        stream: bool = False,
    ) -> None:
        if actor_id not in self._config.allowed_actors:
            raise ValueError("actor not allowed")

        valid = await self._sessions.validate_session(session_id, actor_id)
        if not valid:
            raise ValueError("invalid session")

        envelope = IPCEnvelope(
            trace_id=new_trace_id(),
            session_id=session_id,
            actor_id=actor_id,
            created_monotonic=self._clock.now(),
            priority=priority,
            module_id=module_id,
            method=method,
            payload=payload,
            stream=stream,
        )

        await self._ipc.accept_inbound(envelope)
