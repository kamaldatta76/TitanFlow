"""Packaged Octopus-compatible runtime for TitanOcta.

This adapter is self-contained and importable outside the source repo tree.
It intentionally exposes only the pieces TitanOcta Free needs:

- models_module() -> TaskIn, AgentStatusUpdate
- db_module() -> Database
- governance_module() -> DispatchPlan, GovernanceEngine, _build_dispatch_plan
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import re
from functools import lru_cache
from pathlib import Path
import time
from types import ModuleType
from typing import Any, Awaitable, Callable, Optional
import uuid

import aiosqlite
from pydantic import BaseModel, Field

GOVERNANCE_HMAC_SECRET = "titan-governance-dev-key"

MENTION_TARGETS = {
    "@archie": "archie",
    "@charlie": "charlie",
    "@cc": "cc",
    "@cece": "cc",
    "@cx": "cx",
    "@chex": "cx",
    "@ollie": "ollie",
    "@flow": "flow",
    "@mini": "mini",
}
GREETING_WORDS = {"hello", "hi", "hey", "yo", "sup", "morning", "afternoon", "evening"}
CHECKIN_PHRASES = (
    "you here", "are you here", "you awake", "are you awake",
    "check in", "checkin", "ping", "what's up", "whats up",
)
UI_TERMS = {
    "ui", "ux", "frontend", "front-end", "react", "css", "html", "component",
    "button", "layout", "page", "screen", "bubble", "header", "modal", "form",
    "input", "landing", "copy", "design", "style", "visual", "user-facing",
}
INFRA_TERMS = {
    "ssh", "server", "infra", "infrastructure", "backend", "deploy", "deployment",
    "docker", "systemd", "service", "nginx", "apache", "mercury", "sarge", "shadow",
    "shark", "port", "proxy", "route", "routing", "tunnel", "pangolin", "firewall",
    "dns", "ssl", "database", "postgres", "redis", "worker", "restart",
}
PRODUCT_TERMS = {
    "product", "strategy", "roadmap", "launch", "positioning", "market", "pricing",
    "release", "vision", "direction", "tradeoff", "trade-off", "prioritize",
}
REASONING_TERMS = {
    "diagnose", "diagnosis", "reason", "reasoning", "why", "risk", "architecture",
    "architectural", "coherence", "governance", "review", "decision", "spec",
    "proposal", "plan", "design", "investigate", "issue", "problem", "broken",
    "failing", "failure", "timeout", "timing out",
}
SCOUT_TERMS = {
    "triage", "preflight", "pre-flight", "quick scan", "scan first", "summarize",
    "summary", "recon", "reconnaissance", "first pass", "first-pass",
}
CODE_TERMS = {
    "fix", "bug", "implement", "build", "refactor", "patch", "code", "function",
    "module", "handler", "route", "test",
}
ACTION_TERMS = {
    "fix", "deploy", "restart", "patch", "implement", "build", "update", "edit",
    "change", "ship", "roll out", "rollback", "back up", "upload",
}
SIMPLE_QUESTION_PREFIXES = (
    "what is", "who is", "where is", "when is", "can you", "are you", "do you",
)
STRATEGY_QUESTION_PREFIXES = (
    "should we", "what should we", "how should we",
)
DIAGNOSTIC_PHRASES = (
    "why is", "why are", "why does", "why do", "what happened", "investigate why",
    "figure out why", "help me understand", "what should we do about",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class TaskIn(BaseModel):
    id: Optional[str] = None
    intent: str = ""
    status: str = "planned"
    assigned: str = ""
    provider: str = ""
    model: str = ""
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentStatusUpdate(BaseModel):
    status: str
    budget_pct: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Database:
    def __init__(self, conn: aiosqlite.Connection, retention_days: int, retention_max_rows: int):
        self.conn = conn
        self.retention_days = retention_days
        self.retention_max_rows = retention_max_rows

    @classmethod
    async def open(cls, path: str, retention_days: int, retention_max_rows: int) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        db = cls(conn, retention_days, retention_max_rows)
        await db.migrate()
        return db

    async def close(self) -> None:
        await self.conn.close()

    async def migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY,
          intent TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'planned',
          assigned TEXT NOT NULL DEFAULT '',
          provider TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          priority INTEGER NOT NULL DEFAULT 0,
          created_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS agents (
          id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL DEFAULT '',
          provider TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'offline',
          budget_pct REAL NOT NULL DEFAULT 0.0,
          updated_at_ms INTEGER NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at_ms INTEGER NOT NULL,
          event_id TEXT DEFAULT '',
          room_id TEXT DEFAULT '',
          msg_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS memory (
          scope TEXT NOT NULL,
          key TEXT NOT NULL,
          value_json TEXT NOT NULL DEFAULT '{}',
          created_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER NOT NULL,
          PRIMARY KEY (scope, key)
        );
        """
        await self.conn.executescript(schema)
        await self.conn.commit()

    async def get_tasks(self, status: Optional[str], assigned: Optional[str], limit: int) -> list[dict[str, Any]]:
        q = "SELECT * FROM tasks"
        params: list[Any] = []
        clauses = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if assigned:
            clauses.append("assigned = ?")
            params.append(assigned)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY updated_at_ms DESC LIMIT ?"
        params.append(limit)
        async with self.conn.execute(q, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        async with self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def upsert_task(self, task: TaskIn, created_at_ms: Optional[int] = None) -> dict[str, Any]:
        now = _now_ms()
        ts = created_at_ms or now
        await self.conn.execute(
            """
            INSERT INTO tasks (id, intent, status, assigned, provider, model, priority, created_at_ms, updated_at_ms, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              intent = excluded.intent,
              status = excluded.status,
              assigned = excluded.assigned,
              provider = excluded.provider,
              model = excluded.model,
              priority = excluded.priority,
              updated_at_ms = excluded.updated_at_ms,
              metadata_json = excluded.metadata_json
            """,
            (
                task.id,
                task.intent,
                task.status,
                task.assigned,
                task.provider,
                task.model,
                task.priority,
                ts,
                now,
                json.dumps(task.metadata or {}),
            ),
        )
        await self.conn.commit()
        return await self.get_task(task.id)

    async def set_agent_status(self, agent_id: str, upd: AgentStatusUpdate) -> dict[str, Any]:
        now = _now_ms()
        await self.conn.execute(
            """
            INSERT INTO agents (id, status, budget_pct, updated_at_ms, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              status = excluded.status,
              budget_pct = excluded.budget_pct,
              updated_at_ms = excluded.updated_at_ms,
              metadata_json = excluded.metadata_json
            """,
            (agent_id, upd.status.strip().lower(), float(upd.budget_pct), now, json.dumps(upd.metadata or {})),
        )
        await self.conn.commit()
        async with self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)) as cur:
            row = await cur.fetchone()
        return self._row_to_agent(row)

    async def get_agents(self) -> list[dict[str, Any]]:
        async with self.conn.execute("SELECT * FROM agents ORDER BY updated_at_ms DESC") as cur:
            rows = await cur.fetchall()
        return [self._row_to_agent(row) for row in rows]

    async def append_event(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        created_at_ms: int,
        event_id: str = "",
        room_id: str = "",
        msg_id: str = "",
    ) -> None:
        await self.conn.execute(
            "INSERT INTO events (event_type, source, payload_json, created_at_ms, event_id, room_id, msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_type, source, json.dumps(payload or {}), created_at_ms, event_id, room_id, msg_id),
        )
        await self.conn.commit()

    async def set_memory(self, scope: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        now = _now_ms()
        await self.conn.execute(
            """
            INSERT INTO memory (scope, key, value_json, created_at_ms, updated_at_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, key) DO UPDATE SET
              value_json = excluded.value_json,
              updated_at_ms = excluded.updated_at_ms
            """,
            (scope, key, json.dumps(value or {}), now, now),
        )
        await self.conn.commit()
        return await self.get_memory(scope, key)

    async def get_memory(self, scope: str, key: str) -> Optional[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT scope, key, value_json, created_at_ms, updated_at_ms FROM memory WHERE scope = ? AND key = ?",
            (scope, key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "scope": row["scope"],
            "key": row["key"],
            "value": json.loads(row["value_json"] or "{}"),
            "created_at_ms": row["created_at_ms"],
            "updated_at_ms": row["updated_at_ms"],
        }

    async def list_memory(self, scope_prefix: str) -> list[dict[str, Any]]:
        like = scope_prefix + "%" if scope_prefix.endswith(":") else scope_prefix
        async with self.conn.execute(
            "SELECT scope, key, value_json, created_at_ms, updated_at_ms FROM memory WHERE scope LIKE ? ORDER BY updated_at_ms DESC",
            (like,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "scope": row["scope"],
                "key": row["key"],
                "value": json.loads(row["value_json"] or "{}"),
                "created_at_ms": row["created_at_ms"],
                "updated_at_ms": row["updated_at_ms"],
            }
            for row in rows
        ]

    def _row_to_task(self, row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "intent": row["intent"],
            "status": row["status"],
            "assigned": row["assigned"],
            "provider": row["provider"],
            "model": row["model"],
            "priority": row["priority"],
            "created_at_ms": row["created_at_ms"],
            "updated_at_ms": row["updated_at_ms"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _row_to_agent(self, row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "display_name": row["display_name"],
            "provider": row["provider"],
            "status": row["status"],
            "budget_pct": row["budget_pct"],
            "updated_at_ms": row["updated_at_ms"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }


def _detect_mention_target(intent: str) -> str | None:
    lowered = intent.lower()
    for token, target in MENTION_TARGETS.items():
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", lowered):
            return target
    if re.search(r"(^|\b)(cc|cece)\b", lowered):
        return "cc"
    if re.search(r"(^|\b)(cx|chex)\b", lowered):
        return "cx"
    return None


def _requires_factory_fork(intent: str, mention_target: str | None) -> bool:
    lowered = intent.lower()
    if mention_target in {"cc", "cx"}:
        return True
    return bool(
        re.search(r"\bowner\s*:\s*(cc|cece|cx|chex)\b", lowered)
        or re.search(r"\b(assign|assigned|route|send|for|to)\s+(cc|cece|cx|chex)\b", lowered)
    )


def _is_simple_greeting(intent: str) -> bool:
    lowered = intent.lower().strip()
    words = lowered.split()
    if any(phrase in lowered for phrase in CHECKIN_PHRASES):
        return True
    if lowered.rstrip("?!.,") in GREETING_WORDS:
        return True
    if len(words) <= 4 and any(word in GREETING_WORDS for word in words):
        return True
    return False


def _classify_intent(intent: str) -> str:
    lowered = intent.lower().strip()
    words = lowered.split()
    has_ui = any(term in lowered for term in UI_TERMS)
    has_infra = any(term in lowered for term in INFRA_TERMS)
    has_product = any(term in lowered for term in PRODUCT_TERMS)
    has_reasoning = any(term in lowered for term in REASONING_TERMS)
    has_scout = any(term in lowered for term in SCOUT_TERMS)
    has_code = any(term in lowered for term in CODE_TERMS)
    has_action = any(term in lowered for term in ACTION_TERMS)
    has_diagnostic_phrase = any(phrase in lowered for phrase in DIAGNOSTIC_PHRASES)

    if _is_simple_greeting(intent):
        return "greeting"
    if has_scout and not has_action:
        return "scout_prep"
    if (has_reasoning or has_diagnostic_phrase) and not has_action:
        return "reasoning"
    if lowered.startswith(STRATEGY_QUESTION_PREFIXES) or has_product:
        return "product_strategy"
    if has_ui and has_infra:
        return "code_and_infra"
    if has_ui or (has_code and not has_infra and not has_product and "bug" in lowered):
        return "ui_frontend"
    if has_infra:
        return "infra_backend"
    if len(words) <= 10 and any(lowered.startswith(prefix) for prefix in SIMPLE_QUESTION_PREFIXES):
        return "simple_question"
    return "general"


@dataclass(frozen=True)
class DispatchPlan:
    source: str
    classification: str
    primary_agent: str
    response_agents: tuple[str, ...]
    execution_targets: tuple[str, ...]
    mode: str
    reason: str
    mention_target: str | None = None
    notify_agents: tuple[str, ...] = ()
    requires_executor_touch: bool = False
    close_guard_targets: tuple[str, ...] = ()
    close_guard_policy: str = "none"
    required_subagent_lanes: tuple[str, ...] = ()
    sweep_passes_required: int = 1

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def _build_dispatch_plan(intent: str) -> DispatchPlan:
    mention_target = _detect_mention_target(intent)
    if mention_target == "charlie":
        return DispatchPlan("mention", "mention_override", "charlie", ("charlie",), (), "direct_response", "Explicit @Charlie mention overrides classifier.", mention_target)
    if mention_target == "archie":
        return DispatchPlan("mention", "mention_override", "archie", ("archie",), (), "direct_response", "Explicit @Archie mention overrides classifier.", mention_target)
    if mention_target == "ollie":
        return DispatchPlan("mention", "mention_override", "charlie", ("charlie",), ("ollie",), "spec_then_dispatch", "Explicit @Ollie mention pins execution to Ollie; Charlie translates first.", mention_target)
    if mention_target == "flow":
        return DispatchPlan("mention", "mention_override", "charlie", ("charlie",), ("flow",), "spec_then_dispatch", "Explicit @Flow mention pins execution to Flow; Charlie translates first.", mention_target)
    if mention_target == "mini":
        return DispatchPlan(
            "mention",
            "mention_override",
            "charlie",
            ("charlie",),
            ("mini",),
            "spec_then_dispatch",
            (
                "Explicit @Mini mention routes to Mini scout lane only. "
                "Mini is preflight utility, never chain authority; Charlie translates first."
            ),
            mention_target,
        )
    if _requires_factory_fork(intent, mention_target):
        return DispatchPlan(
            "mention" if mention_target else "classifier",
            "golden_role_factory",
            "charlie",
            ("charlie",),
            ("ollie", "flow"),
            "spec_then_dispatch",
            (
                "Golden role doctrine: CC/Chex task auto-routes through factory execution. "
                "Charlie translates first, then Ollie+Flow execute, with mandatory Dash/Octa/Flow subagent lanes "
                "and a second sweep before close."
            ),
            mention_target,
            notify_agents=("cc", "cx"),
            requires_executor_touch=True,
            close_guard_targets=("ollie", "flow"),
            close_guard_policy="all",
            required_subagent_lanes=("dash", "octa", "flow"),
            sweep_passes_required=2,
        )

    classification = _classify_intent(intent)
    if classification in {"greeting", "simple_question", "general"}:
        return DispatchPlan("classifier", classification, "charlie", ("charlie",), (), "direct_response", "Simple conversational traffic stays at Charlie on the top layer.")
    if classification == "product_strategy":
        return DispatchPlan("classifier", classification, "charlie", ("charlie",), (), "direct_response", "Product and strategy questions stay with Charlie unless explicitly escalated.")
    if classification == "reasoning":
        return DispatchPlan("classifier", classification, "archie", ("archie", "charlie"), (), "direct_response", "Diagnosis and reasoning prompts get Archie's analysis before Charlie's verdict.")
    if classification == "scout_prep":
        return DispatchPlan(
            "classifier",
            classification,
            "charlie",
            ("charlie",),
            ("mini",),
            "spec_then_dispatch",
            (
                "Scout/preflight requests go through Mini utility lane for cheap prep; "
                "Charlie remains decision authority."
            ),
        )
    if classification == "ui_frontend":
        return DispatchPlan("classifier", classification, "charlie", ("charlie",), ("ollie",), "spec_then_dispatch", "UI and user-facing code routes to Ollie, but Charlie must translate first.")
    if classification == "infra_backend":
        return DispatchPlan("classifier", classification, "charlie", ("charlie",), ("flow",), "spec_then_dispatch", "Infra and deployment work routes to Flow, but Charlie must translate first.")
    return DispatchPlan("classifier", "code_and_infra", "charlie", ("charlie",), ("ollie", "flow"), "spec_then_dispatch", "Mixed code and infra work is specified once by Charlie, then dispatched in parallel.")


def dispatch_close_guard_satisfied(dispatch: dict[str, Any], touched_targets: list[str] | tuple[str, ...] | set[str]) -> bool:
    policy = str(dispatch.get("close_guard_policy", "none")).lower()
    targets = {
        str(t).lower()
        for t in (dispatch.get("close_guard_targets") or [])
        if str(t).strip()
    }
    touched = {str(t).lower() for t in touched_targets if str(t).strip()}
    if policy == "none" or not targets:
        target_gate_ok = True
    elif policy == "all":
        target_gate_ok = targets.issubset(touched)
    elif policy == "any":
        target_gate_ok = bool(targets.intersection(touched))
    else:
        target_gate_ok = True

    if not target_gate_ok:
        return False

    required_sweeps = int(dispatch.get("sweep_passes_required", 1) or 1)
    completed_sweeps = int(dispatch.get("sweeps_completed", 0) or 0)
    return completed_sweeps >= required_sweeps


def _sign(event: dict[str, Any]) -> str:
    msg = f"{event.get('task_id', '')}:{event.get('created_at_ms', '')}:{event.get('nonce', '')}"
    return hmac.new(GOVERNANCE_HMAC_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


class GovernanceEngine:
    def __init__(self, bus: Any, db: Any):
        self._bus = bus
        self._db = db
        self._counter = 1
        self._counter_lock = asyncio.Lock()
        self._decision_guard: Optional[Callable[[str, str, str, dict[str, Any]], Awaitable[None]]] = None

    def install_decision_guard(
        self,
        guard: Callable[[str, str, str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._decision_guard = guard

    async def load_counter(self) -> None:
        tasks = await self._db.get_tasks(None, None, 500)
        max_n = 0
        for task in tasks:
            tid = task.get("id", "")
            if tid.startswith("GOV-"):
                try:
                    max_n = max(max_n, int(tid[4:]))
                except ValueError:
                    pass
        self._counter = max_n + 1

    async def _next_id(self) -> str:
        async with self._counter_lock:
            decision_id = f"GOV-{self._counter:04d}"
            self._counter += 1
            return decision_id

    def make_event(
        self,
        event_type: str,
        decision_id: str,
        room_id: str,
        actor: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonce = uuid.uuid4().hex
        event = {
            "event": event_type,
            "event_type": event_type,
            "event_id": uuid.uuid4().hex,
            "task_id": decision_id,
            "decision_id": decision_id,
            "room_id": room_id,
            "actor": actor,
            "nonce": nonce,
            "created_at_ms": _now_ms(),
            **(extra or {}),
        }
        event["signature"] = _sign(event)
        return event

    async def create_decision(
        self,
        intent: str,
        actor: str,
        room_id: str = "governance",
        context: dict[str, Any] | None = None,
    ) -> str:
        if self._decision_guard is not None:
            await self._decision_guard(intent, actor, room_id, context or {})
        decision_id = await self._next_id()
        dispatch_plan = _build_dispatch_plan(intent)
        task = TaskIn(
            id=decision_id,
            intent=intent,
            status="pending",
            assigned=actor,
            metadata={
                "room_id": room_id,
                "type": "governance_decision",
                "context": context or {},
                "dispatch": dispatch_plan.to_metadata(),
            },
        )
        await self._db.upsert_task(task)
        await self._bus.publish(
            self.make_event(
                "decision_created",
                decision_id,
                room_id,
                actor,
                {"intent": intent, "context": context or {}, "dispatch": dispatch_plan.to_metadata()},
            )
        )
        return decision_id

    async def stream_responses(
        self,
        decision_id: str,
        intent: str,
        room_id: str,
        actor: str,
        archie_system: str = "",
        charlie_system: str = "",
        response_content: str | None = None,
    ) -> str:
        plan = _build_dispatch_plan(intent)
        content = (response_content or "").strip() or self._compose_response(plan, intent)
        await self._bus.publish(self.make_event("gov_route_selected", decision_id, room_id, actor, {"dispatch": plan.to_metadata()}))
        await self._bus.publish(
            self.make_event(
                "gov_response_end",
                decision_id,
                room_id,
                actor,
                {
                    "panel": plan.primary_agent,
                    "content": content,
                    "dispatch": plan.to_metadata(),
                },
            )
        )
        return content

    def _compose_response(self, plan: DispatchPlan, intent: str) -> str:
        cleaned = " ".join(intent.strip().split())
        if plan.classification == "greeting":
            return "I'm here. TitanOcta is online and the Flow spine is connected."
        if plan.classification == "product_strategy":
            return (
                "Charlie recommendation: keep the product surface simple, keep Flow underneath it, "
                "and avoid adding a second backend."
            )
        if plan.classification == "reasoning":
            return (
                f"Archie assessment: {cleaned} points to a reasoning task, not blind execution. "
                "Charlie verdict: diagnose first, then dispatch with a precise spec."
            )
        if plan.mode == "spec_then_dispatch":
            targets = " + ".join(target.capitalize() for target in plan.execution_targets)
            return f"Charlie spec prepared for {targets}: {cleaned}"
        return f"Charlie received: {cleaned}"


@lru_cache(maxsize=1)
def models_module() -> ModuleType:
    module = ModuleType("titanocta_octopus_models")
    module.TaskIn = TaskIn
    module.AgentStatusUpdate = AgentStatusUpdate
    return module


@lru_cache(maxsize=1)
def db_module() -> ModuleType:
    module = ModuleType("titanocta_octopus_db")
    module.Database = Database
    return module


@lru_cache(maxsize=1)
def governance_module() -> ModuleType:
    module = ModuleType("titanocta_octopus_governance")
    module.DispatchPlan = DispatchPlan
    module.GovernanceEngine = GovernanceEngine
    module._build_dispatch_plan = _build_dispatch_plan
    module.dispatch_close_guard_satisfied = dispatch_close_guard_satisfied
    return module
