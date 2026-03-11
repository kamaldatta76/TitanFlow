"""Single-agent TitanOcta runtime that always uses the governance path."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from .context import ContextInjector, ContextStore, MemoryFlusher
from .credits import CreditMiddleware
from .credits.credit_events import emit_credit_event
from .model_router import ModelRouter, RoutingProfile
from .routing import TitanOctaRouter, TitanOctaRoute
from .tai import TAi
from .tier_guard import TierGuard
from ._octopus import models_module


@dataclass(frozen=True)
class TitanOctaAgentRegistration:
    agent_id: str
    node_id: str
    tier: str
    registered_at_ms: int


class TitanOctaAgentRuntime:
    def __init__(
        self,
        *,
        governance: Any,
        bus: Any,
        agent_id: str = "titan",
        node_id: str = "local",
        db: Any | None = None,
        tier_guard: TierGuard | None = None,
        router: TitanOctaRouter | None = None,
        tai: TAi | None = None,
        model: str | None = None,
        ollama_host: str | None = None,
        credit_middleware: CreditMiddleware | None = None,
        model_router: ModelRouter | None = None,
        provisioning_db_path: str | None = None,
        provisioned_user_id: str | None = None,
        context_store: ContextStore | None = None,
        context_injector: ContextInjector | None = None,
        memory_flusher: MemoryFlusher | None = None,
        context_db_path: str | None = None,
    ) -> None:
        self._governance = governance
        self._bus = bus
        self._agent_id = agent_id
        self._node_id = node_id
        self._db = db
        self._tier_guard = tier_guard or TierGuard()
        self._router = router or TitanOctaRouter()
        self._model = model or os.environ.get("TITANOCTA_MODEL", "qwen2.5:7b")
        self._ollama_host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self._tai = tai
        self._provisioning_db_path = Path(
            provisioning_db_path
            or os.environ.get("TITANOCTA_PROVISIONING_DB", "~/.titanocta/provisioning.sqlite")
        ).expanduser()
        self._provisioned_user_id = provisioned_user_id
        self._credit_middleware = credit_middleware or CreditMiddleware(self._provisioning_db_path)
        self._model_router = model_router or ModelRouter()
        self._context_db_path = Path(
            context_db_path
            or os.environ.get("TITANOCTA_CONTEXT_DB", "~/.titanocta/context.sqlite")
        ).expanduser()
        self._context_store = context_store or ContextStore(self._context_db_path)
        self._context_injector = context_injector or ContextInjector(self._context_store, max_items=5, min_score=0.35)
        self._memory_flusher = memory_flusher or MemoryFlusher(self._context_store, soft_token_limit=1200, min_score=0.35)
        if self._db is not None and hasattr(self._governance, "install_decision_guard"):
            self._governance.install_decision_guard(self._kernel_guard)

    async def register_with_flow(self) -> TitanOctaAgentRegistration:
        registration = TitanOctaAgentRegistration(
            agent_id=self._agent_id,
            node_id=self._node_id,
            tier=self._tier_guard.tier,
            registered_at_ms=int(time.time() * 1000),
        )
        event = self._governance.make_event(
            "titanocta_agent_registered",
            "TITANOCTA-BOOT",
            "titanocta",
            self._agent_id,
            {
                "agent_id": self._agent_id,
                "node_id": self._node_id,
                "tier": self._tier_guard.tier,
            },
        )
        waiter = getattr(self._bus, "wait_for", None)
        if waiter is None:
            raise RuntimeError("TitanFlow registration bus does not support acknowledgments")
        ack_task = asyncio.create_task(
            waiter(
                "titanocta_agent_registration_ack",
                predicate=lambda ack: ack.get("agent_id") == self._agent_id and ack.get("node_id") == self._node_id,
                timeout=5.0,
            )
        )
        await self._bus.publish(event)
        try:
            await ack_task
        except TimeoutError as exc:
            raise RuntimeError("TitanFlow registration ACK timed out") from exc
        if self._db is not None:
            status_update = models_module().AgentStatusUpdate(
                status="online",
                budget_pct=0.0,
                metadata={
                    "tier": self._tier_guard.tier,
                    "node_id": self._node_id,
                    "surface": "titanocta-free",
                },
            )
            await self._db.set_agent_status(self._agent_id, status_update)
            await self._db.set_memory(
                "governance",
                f"titanocta:agent:{self._agent_id}",
                {"agent_id": self._agent_id, "node_id": self._node_id, "registered_at_ms": registration.registered_at_ms},
            )
            await self._db.set_memory(
                "governance",
                f"titanocta:node:{self._node_id}",
                {"node_id": self._node_id, "agent_id": self._agent_id, "registered_at_ms": registration.registered_at_ms},
            )
        return registration

    async def submit_user_message(self, intent: str, *, actor: str = "user", room_id: str = "titanocta") -> str:
        """
        Full governance path: route → tier guard → decision → model → response.
        Returns the model's response string (not the decision ID).
        """
        route = self._router.route(intent)
        billing_user = self._resolve_billing_user(actor)
        profile = self._load_routing_profile(billing_user)
        route_decision = self._model_router.route_model(
            preferred_model=self._model,
            profile=profile,
            prompt=intent,
            soft_cap_engaged=False,
        )
        if route_decision.blocked_event:
            self._append_credit_audit(
                user_id=billing_user,
                event_type=route_decision.blocked_event,
                metadata={
                    "model": self._model,
                    "provider_mode": profile.provider_mode,
                },
            )
        if not route_decision.allowed:
            return route_decision.reason
        self._context_store.add_entry(
            user_id=billing_user,
            session_id=room_id,
            role="user",
            content=intent,
            score=0.9,
        )
        injected = self._context_injector.inject(user_id=billing_user, session_id=room_id)
        try:
            decision_id = await self._governance.create_decision(intent=intent, actor=actor, room_id=room_id)
        except PermissionError as exc:
            return str(exc)
        if self._db is not None:
            await self._db.set_memory(
                "governance",
                f"titanocta:user:{actor}",
                {"actor": actor, "last_room_id": room_id, "last_intent": intent, "updated_at_ms": int(time.time() * 1000)},
            )
        response = await self._call_model(
            intent,
            route,
            model_name=route_decision.model,
            injected_context=injected.as_prompt_block(),
        )
        self._context_store.add_entry(
            user_id=billing_user,
            session_id=room_id,
            role="assistant",
            content=response,
            score=0.85,
        )
        self._memory_flusher.flush_if_needed(user_id=billing_user, session_id=room_id)
        self._debit_if_managed(
            user_id=billing_user,
            intent=intent,
            response=response,
            model_name=route_decision.model,
        )
        if self._tai is not None:
            self._tai.update_after_response(response, route.classification)
            if "model unavailable" in response.lower():
                self._tai.record_signal("negative")
        self._router.append_verification_audit(
            decision_id=decision_id,
            verification_record={
                "requirements_covered": [route.classification],
                "risks": [],
                "deviations": [],
                "evidence_links": [],
                "status": "pass",
            },
        )
        self._router.append_synthesized_audit(
            decision_id=decision_id,
            actor=actor,
            response=response,
            upstream_target="papa_or_ac",
        )
        await self._governance.stream_responses(
            decision_id=decision_id,
            intent=intent,
            room_id=room_id,
            actor=actor,
            response_content=response,
        )
        return response

    async def _call_model(
        self,
        intent: str,
        route: TitanOctaRoute,
        *,
        model_name: str,
        injected_context: str = "",
    ) -> str:
        """Call Ollama with a system prompt shaped by the governance route."""
        try:
            import ollama  # noqa: PLC0415 — lazy import, ollama is a hard dep post-install
            client = ollama.AsyncClient(host=self._ollama_host)
            messages = [{"role": "system", "content": self._system_for_route(route)}]
            if injected_context:
                messages.append({"role": "system", "content": injected_context})
            messages.append({"role": "user", "content": intent})
            response = await client.chat(
                model=model_name,
                messages=messages,
            )
            return str(response.message.content).strip()
        except Exception as exc:  # noqa: BLE001
            return f"[Model unavailable — is Ollama running? ({exc})]"

    @staticmethod
    def _system_for_route(route: TitanOctaRoute) -> str:
        base = (
            "You are Titan, a governed AI agent running locally on this machine. "
            "You are part of TitanOcta Free v1.0. "
            "Be concise, direct, and accurate. "
            "Never fabricate facts. If you don't know, say so clearly."
        )
        extra: dict[str, str] = {
            "reasoning": (
                " Your role for this message is to diagnose, analyse, and reason carefully before answering. "
                "Walk through the problem step by step."
            ),
            "product_strategy": (
                " Your role for this message is to give clear, grounded strategic and product direction."
            ),
            "infra_backend": (
                " Your role for this message is to give precise infrastructure, deployment, and backend guidance."
            ),
            "ui_frontend": (
                " Your role for this message is to give clear UI and frontend implementation guidance."
            ),
            "code_and_infra": (
                " Your role for this message is to address both code and infrastructure concerns clearly."
            ),
        }
        return base + extra.get(route.classification, "")

    async def _kernel_guard(
        self,
        intent: str,
        actor: str,
        room_id: str,
        context: dict[str, Any],
    ) -> None:
        if self._db is None:
            raise RuntimeError("TitanOcta kernel guard requires a live governance DB")
        counts = await self._current_counts(actor)
        self._tier_guard.raise_if_blocked(**counts)

    async def _current_counts(self, actor: str) -> dict[str, int]:
        agents = {
            item["key"].split("titanocta:agent:", 1)[1]
            for item in await self._db.list_memory("governance")
            if item["key"].startswith("titanocta:agent:")
        }
        nodes = {
            item["key"].split("titanocta:node:", 1)[1]
            for item in await self._db.list_memory("governance")
            if item["key"].startswith("titanocta:node:")
        }
        users = {
            item["key"].split("titanocta:user:", 1)[1]
            for item in await self._db.list_memory("governance")
            if item["key"].startswith("titanocta:user:")
        }
        users.add(actor)
        return {
            "users": len(users),
            "agents": len(agents),
            "nodes": len(nodes),
        }

    @property
    def model(self) -> str:
        return self._model

    @property
    def router(self) -> TitanOctaRouter:
        return self._router

    @property
    def tai(self) -> TAi | None:
        return self._tai

    def _resolve_billing_user(self, actor: str) -> str:
        if self._provisioned_user_id:
            return self._provisioned_user_id
        return actor

    def _load_routing_profile(self, user_id: str) -> RoutingProfile:
        fallback = RoutingProfile(
            available_models=("ollama-local",),
            excluded_models=(),
            provider_mode="western_only",
            soft_cap_strategy=None,
            warning_thresholds=(0.80, 0.95),
            hard_cap=False,
            redirect_to_thor=False,
            content_filter=None,
        )
        if not self._provisioning_db_path.exists():
            return fallback
        conn = sqlite3.connect(self._provisioning_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from octa_users where user_id = ?", (user_id,)).fetchone()
        conn.close()
        if row is None:
            return fallback
        import json

        return RoutingProfile(
            available_models=tuple(json.loads(row["available_models"] or "[]")),
            excluded_models=tuple(json.loads(row["excluded_models"] or "[]")),
            provider_mode=str(row["provider_mode"]),
            soft_cap_strategy=row["soft_cap_strategy"],
            warning_thresholds=tuple(json.loads(row["warning_thresholds"] or "[]")),
            hard_cap=bool(row["hard_cap"]),
            redirect_to_thor=bool(row["redirect_to_thor"]) if "redirect_to_thor" in row.keys() else False,
            content_filter=row["content_filter"] if "content_filter" in row.keys() else None,
        )

    def _debit_if_managed(self, *, user_id: str, intent: str, response: str, model_name: str) -> None:
        cost = self._estimate_model_cost(intent=intent, response=response, model_name=model_name)
        if cost <= 0.0:
            return
        try:
            self._credit_middleware.debit_credit(user_id, cost)
        except Exception:  # noqa: BLE001
            # Credit middleware is non-fatal for response delivery.
            return

    @staticmethod
    def _estimate_model_cost(*, intent: str, response: str, model_name: str) -> float:
        # Local models are not billed in managed credits.
        local_markers = ("qwen2.5", "ollama", "local")
        lowered = model_name.lower()
        if any(marker in lowered for marker in local_markers):
            return 0.0

        prices = {
            "gpt-5-nano": (0.05, 0.40),
            "gpt-5-mini": (0.25, 2.00),
            "claude-haiku-4-5": (0.80, 4.00),
            "claude-sonnet-4-6": (3.00, 15.00),
            "minimax-m2.5": (0.30, 1.20),
            "qwen-flash": (0.20, 0.80),
        }
        in_rate, out_rate = prices.get(lowered, (0.0, 0.0))
        if in_rate == 0.0 and out_rate == 0.0:
            return 0.0
        in_tokens = max(1, len(intent) // 4)
        out_tokens = max(1, len(response) // 4)
        return ((in_tokens * in_rate) + (out_tokens * out_rate)) / 1_000_000.0

    def _append_credit_audit(self, *, user_id: str, event_type: str, metadata: dict[str, object]) -> None:
        if not self._provisioning_db_path.exists():
            return
        try:
            conn = sqlite3.connect(self._provisioning_db_path)
            emit_credit_event(
                conn,
                user_id=user_id,
                event_type=event_type,
                metadata=metadata,
            )
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001
            return
