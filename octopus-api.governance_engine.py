"""
GOV-0001 — Governance Engine
Room-scoped locks, response mutex, 12s deadlock timeout, HMAC event signing.
Sequential panel streaming: Archie first, Charlie second per decision.
"""

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

GOVERNANCE_HMAC_SECRET = os.getenv("GOVERNANCE_HMAC_SECRET", "titan-governance-dev-key")
DEADLOCK_TIMEOUT_S = 120          # Seconds before mutex auto-releases (deadlock guard)
STREAM_HUNG_TIMEOUT_S = 120      # Seconds before giving up on a live stream
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
ARCHIE_GOV_MODEL = os.getenv("ARCHIE_GOV_MODEL", "anthropic/claude-sonnet-4-6")
# Charlie uses Alibaba DashScope (OpenAI-compatible) — qwen-max for governance rigor
ALIBABA_API_KEY = os.getenv("ALIBABA_API_KEY", "")
ALIBABA_BASE_URL = os.getenv("ALIBABA_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
CHARLIE_GOV_MODEL = os.getenv("CHARLIE_GOV_MODEL", "qwen-max")
CHARLIE_OR_MODEL = os.getenv("CHARLIE_OR_MODEL", "qwen/qwen-2.5-72b-instruct")  # OpenRouter fallback when Alibaba key is unavailable

ARCHIE_DEFAULT_SYSTEM = (
    "You are Archie — TitanArray's resident architect in the Master Governance Chamber.\n"
    "Charlie is the CTO. He speaks after you. You are the first voice only.\n"
    "\n"
    "You only appear on genuine governance decisions — design review, risk surface, "
    "architectural coherence. Papa's check-ins and direct addresses to Charlie "
    "never reach you.\n"
    "\n"
    "RULES:\n"
    "  - No greeting. No 'Hello.' First word is your assessment.\n"
    "  - Flag risks clearly. Propose with specificity.\n"
    "  - Max 200 words.\n"
)
CHARLIE_DEFAULT_SYSTEM = (
    "You are Charlie — TitanArray's CTO in the Master Governance Chamber.\n"
    "\n"
    "Read Papa's message and choose the correct mode:\n"
    "\n"
    "MODE 1 — CHECK-IN or GREETING\n"
    "If the message is a check-in, ping, greeting, or short direct address "
    "('you here?', 'Charlie?', 'hello', 'you awake?', anything under ~10 words "
    "that is not a real decision), respond with ONLY:\n"
    "  Here Papa.\n"
    "Nothing else. No analysis. No headings.\n"
    "\n"
    "MODE 2 — GOVERNANCE DECISION\n"
    "If the message contains a real spec, proposal, or architectural question:\n"
    "SCOPE: What does this actually change? One sentence.\n"
    "RISK: What breaks? Probability and blast radius.\n"
    "GAPS: What is Archie missing or underestimating?\n"
    "VERDICT: APPROVE / REJECT / APPROVE-WITH-CONDITIONS — one line, no hedging.\n"
    "\n"
    "MODE 2 rules:\n"
    "  - No greeting. First word is 'SCOPE:'.\n"
    "  - Max 250 words.\n"
    "  - Push back on Archie if warranted.\n"
)
CHARLIE_DISPATCH_SYSTEM = (
    "You are Charlie — TitanArray's chief operator.\n"
    "\n"
    "Your job is not to solve the task yourself. Your job is to translate Papa's request "
    "into an execution-ready spec for Ollie and/or Flow.\n"
    "\n"
    "RULES:\n"
    "  - Do not forward the raw request.\n"
    "  - Be precise and concrete.\n"
    "  - Output only these sections:\n"
    "OBJECTIVE:\n"
    "CONTEXT:\n"
    "CONSTRAINTS:\n"
    "EXECUTION:\n"
    "VERIFICATION:\n"
)

logger = logging.getLogger(__name__)
ExecutorHandler = Callable[[dict[str, Any]], Awaitable[None]]
DEFAULT_ROUTING_AUDIT_LOG = "/tmp/titanflow-routing-audit.jsonl"


def _now_ms() -> int:
    return int(time.time() * 1000)


MENTION_TARGETS = {
    "@archie": "archie",
    "@charlie": "charlie",
    "@cc": "cc",
    "@cece": "cc",
    "@cx": "cx",
    "@chex": "cx",
    "@ollie": "ollie",
    "@flow": "flow",
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


class DispatchWorkerConsumer:
    """Consumes gov_dispatch_target events and hands executors Charlie's spec only."""

    def __init__(
        self,
        bus: Any,
        *,
        audit_log_path: str | None = None,
    ) -> None:
        self._bus = bus
        self._handlers: dict[str, ExecutorHandler] = {}
        self._audit_log_path = Path(audit_log_path or DEFAULT_ROUTING_AUDIT_LOG)
        self._subscribed = False

    def register_executor(self, target: str, handler: ExecutorHandler) -> None:
        self._handlers[target.lower()] = handler

    def start(self) -> None:
        if self._subscribed or not hasattr(self._bus, "subscribe"):
            return
        self._bus.subscribe("gov_dispatch_target", self._handle_event)
        self._subscribed = True

    async def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("event_type") != "gov_dispatch_target":
            return

        payload = self._build_executor_payload(event)
        target = payload["target"]
        await self._append_audit("executor_dispatch", payload)

        handler = self._handlers.get(target)
        if handler is None:
            await self._append_audit(
                "executor_missing",
                {
                    "decision_id": payload["decision_id"],
                    "target": target,
                },
            )
            return

        await handler(payload)
        await self._append_audit(
            "executor_complete",
            {
                "decision_id": payload["decision_id"],
                "target": target,
            },
        )

    @staticmethod
    def _build_executor_payload(event: dict[str, Any]) -> dict[str, Any]:
        dispatch = dict(event.get("dispatch") or {})
        payload = {
            "decision_id": event.get("decision_id") or event.get("task_id"),
            "room_id": event.get("room_id"),
            "actor": event.get("actor"),
            "target": str(event.get("target", "")).lower(),
            "spec": event.get("spec", ""),
            "dispatch": dispatch,
        }
        return payload

    async def _append_audit(self, stage: str, payload: dict[str, Any]) -> None:
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts_ms": _now_ms(),
            "stage": stage,
            **payload,
        }
        with self._audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")


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
    has_code = any(term in lowered for term in CODE_TERMS)
    has_action = any(term in lowered for term in ACTION_TERMS)
    has_diagnostic_phrase = any(phrase in lowered for phrase in DIAGNOSTIC_PHRASES)

    if _is_simple_greeting(intent):
        return "greeting"
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


def _build_dispatch_plan(intent: str) -> DispatchPlan:
    mention_target = _detect_mention_target(intent)
    if mention_target == "charlie":
        return DispatchPlan(
            source="mention",
            classification="mention_override",
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=(),
            mode="direct_response",
            reason="Explicit @Charlie/CC mention overrides classifier.",
            mention_target=mention_target,
        )
    if mention_target == "archie":
        return DispatchPlan(
            source="mention",
            classification="mention_override",
            primary_agent="archie",
            response_agents=("archie",),
            execution_targets=(),
            mode="direct_response",
            reason="Explicit @Archie mention overrides classifier.",
            mention_target=mention_target,
        )
    if mention_target == "ollie":
        return DispatchPlan(
            source="mention",
            classification="mention_override",
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=("ollie",),
            mode="spec_then_dispatch",
            reason="Explicit @Ollie mention pins execution to Ollie; Charlie translates first.",
            mention_target=mention_target,
        )
    if mention_target == "flow":
        return DispatchPlan(
            source="mention",
            classification="mention_override",
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=("flow",),
            mode="spec_then_dispatch",
            reason="Explicit @Flow mention pins execution to Flow; Charlie translates first.",
            mention_target=mention_target,
        )

    if _requires_factory_fork(intent, mention_target):
        return DispatchPlan(
            source="mention" if mention_target else "classifier",
            classification="golden_role_factory",
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=("ollie", "flow"),
            mode="spec_then_dispatch",
            reason=(
                "Golden role doctrine: CC/Chex task auto-routes through factory execution. "
                "Charlie translates first, then Ollie+Flow execute, with mandatory Dash/Octa/Flow subagent lanes "
                "and a second sweep before close."
            ),
            mention_target=mention_target,
            notify_agents=("cc", "cx"),
            requires_executor_touch=True,
            close_guard_targets=("ollie", "flow"),
            close_guard_policy="all",
            required_subagent_lanes=("dash", "octa", "flow"),
            sweep_passes_required=2,
        )

    classification = _classify_intent(intent)
    if classification in {"greeting", "simple_question", "general"}:
        return DispatchPlan(
            source="classifier",
            classification=classification,
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=(),
            mode="direct_response",
            reason="Simple conversational traffic stays at Charlie on the top layer.",
        )
    if classification == "product_strategy":
        return DispatchPlan(
            source="classifier",
            classification=classification,
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=(),
            mode="direct_response",
            reason="Product and strategy questions stay with Charlie unless explicitly escalated.",
        )
    if classification == "reasoning":
        return DispatchPlan(
            source="classifier",
            classification=classification,
            primary_agent="archie",
            response_agents=("archie", "charlie"),
            execution_targets=(),
            mode="direct_response",
            reason="Diagnosis and reasoning prompts get Archie's analysis before Charlie's verdict.",
        )
    if classification == "ui_frontend":
        return DispatchPlan(
            source="classifier",
            classification=classification,
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=("ollie",),
            mode="spec_then_dispatch",
            reason="UI and user-facing code routes to Ollie, but Charlie must translate first.",
        )
    if classification == "infra_backend":
        return DispatchPlan(
            source="classifier",
            classification=classification,
            primary_agent="charlie",
            response_agents=("charlie",),
            execution_targets=("flow",),
            mode="spec_then_dispatch",
            reason="Infra and deployment work routes to Flow, but Charlie must translate first.",
        )
    return DispatchPlan(
        source="classifier",
        classification="code_and_infra",
        primary_agent="charlie",
        response_agents=("charlie",),
        execution_targets=("ollie", "flow"),
        mode="spec_then_dispatch",
        reason="Mixed code and infra work is specified once by Charlie, then dispatched in parallel.",
    )


def _is_charlie_addressed(intent: str) -> bool:
    plan = _build_dispatch_plan(intent)
    return plan.response_agents == ("charlie",) and not plan.execution_targets


def _build_dispatch_prompt(intent: str, plan: DispatchPlan) -> str:
    targets = ", ".join(target.title() for target in plan.execution_targets)
    split_instruction = ""
    if len(plan.execution_targets) > 1:
        split_instruction = (
            "\nReturn one fully independent spec block per execution target in this exact format:\n"
            "=== OLLIE SPEC ===\n"
            "OBJECTIVE:\nCONTEXT:\nCONSTRAINTS:\nEXECUTION:\nVERIFICATION:\n"
            "=== FLOW SPEC ===\n"
            "OBJECTIVE:\nCONTEXT:\nCONSTRAINTS:\nEXECUTION:\nVERIFICATION:\n"
            "Do not merge the Ollie and Flow work into one shared block.\n"
        )
    coordination_instruction = ""
    if plan.requires_executor_touch:
        coordination_instruction = (
            "\nFACTORY DOCTRINE:\n"
            "- CC and Chex must be explicitly notified of both target specs.\n"
            "- Execution is complete only after both targets (Ollie and Flow) report touch.\n"
            "- Required subagent lanes: Dash, Octa, Flow.\n"
            "- Two sweeps are mandatory: initial execution sweep, then a second audit sweep.\n"
            "- Add a COORDINATION section with handoff/ack lines for both targets.\n"
        )
    return (
        f"Papa's request:\n{intent}\n\n"
        f"Execution targets: {targets}\n"
        f"Routing reason: {plan.reason}\n"
        f"{split_instruction}"
        f"{coordination_instruction}\n"
        "Translate this into an execution-ready spec. Do not solve it yourself."
    )


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


def _split_dispatch_specs(spec_text: str, execution_targets: tuple[str, ...]) -> dict[str, str]:
    if len(execution_targets) <= 1:
        return {execution_targets[0]: spec_text.strip()} if execution_targets else {}

    split_specs: dict[str, str] = {}
    for target in execution_targets:
        marker = f"=== {target.upper()} SPEC ==="
        next_markers = [f"=== {other.upper()} SPEC ===" for other in execution_targets if other != target]
        if marker not in spec_text:
            continue
        section = spec_text.split(marker, 1)[1]
        next_positions = [section.find(next_marker) for next_marker in next_markers if next_marker in section]
        if next_positions:
            section = section[: min(pos for pos in next_positions if pos >= 0)]
        split_specs[target] = section.strip()

    if len(split_specs) == len(execution_targets):
        return split_specs

    common = spec_text.strip()
    return {
        target: (
            f"TARGET: {target.title()}\n"
            f"{common}"
        ).strip()
        for target in execution_targets
    }


def _sign(event: dict[str, Any]) -> str:
    msg = f"{event.get('task_id', '')}:{event.get('created_at_ms', '')}:{event.get('nonce', '')}"
    return hmac.new(
        GOVERNANCE_HMAC_SECRET.encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()


class RoomState:
    """Per-room lock state. Typing lock and response mutex are independent."""

    def __init__(self, room_id: str, bus: Any):
        self.room_id = room_id
        self.typing_locked: bool = False
        self.typing_actor: str = ""
        self.response_mutex: bool = False
        self.active_panel: str = ""
        self._bus = bus
        self._lock = asyncio.Lock()
        self._deadlock_task: Optional[asyncio.Task] = None

    async def set_typing(self, actor: str, is_typing: bool) -> None:
        """Set/release typing lock — only affects this room."""
        async with self._lock:
            self.typing_locked = is_typing
            self.typing_actor = actor if is_typing else ""

    async def acquire_mutex(self, panel: str) -> bool:
        """Acquire response mutex for a panel. Returns False if already held."""
        async with self._lock:
            if self.response_mutex:
                return False
            self.response_mutex = True
            self.active_panel = panel
            self._deadlock_task = asyncio.create_task(self._deadlock_guard(panel))
            return True

    async def release_mutex(self) -> None:
        """Release response mutex, cancel deadlock guard."""
        async with self._lock:
            self.response_mutex = False
            self.active_panel = ""
            if self._deadlock_task and not self._deadlock_task.done():
                self._deadlock_task.cancel()
                self._deadlock_task = None

    async def _deadlock_guard(self, panel: str) -> None:
        """Auto-release after DEADLOCK_TIMEOUT_S and publish audit event."""
        await asyncio.sleep(DEADLOCK_TIMEOUT_S)
        released = False
        async with self._lock:
            if self.response_mutex:
                self.response_mutex = False
                self.active_panel = ""
                self._deadlock_task = None
                released = True
        if released:
            try:
                await self._bus.publish({
                    "event": "gov_deadlock_release",
                    "event_type": "gov_deadlock_release",
                    "room_id": self.room_id,
                    "panel": panel,
                    "timeout_s": DEADLOCK_TIMEOUT_S,
                    "audit": "deadlock_auto_release",
                    "created_at_ms": _now_ms(),
                })
            except Exception:
                pass


class GovernanceEngine:
    """Creates decisions (GOV-XXXX), manages rooms, signs events, coordinates streaming."""

    def __init__(self, bus: Any, db: Any):
        self._bus = bus
        self._db = db
        self._rooms: dict[str, RoomState] = {}
        self._counter_lock = asyncio.Lock()
        self._counter = 1
        self._routing_audit_log = Path(
            os.getenv("TITANFLOW_ROUTING_AUDIT_LOG", DEFAULT_ROUTING_AUDIT_LOG)
        )
        self._worker_consumer = DispatchWorkerConsumer(
            bus,
            audit_log_path=str(self._routing_audit_log),
        )
        self._worker_consumer.start()

    def register_executor(self, target: str, handler: ExecutorHandler) -> None:
        self._worker_consumer.register_executor(target, handler)

    async def _append_routing_audit(self, stage: str, payload: dict[str, Any]) -> None:
        self._routing_audit_log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts_ms": _now_ms(),
            "stage": stage,
            **payload,
        }
        with self._routing_audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")

    async def load_counter(self) -> None:
        """Resume GOV-XXXX counter from DB after service restart."""
        try:
            tasks = await self._db.get_tasks(None, None, 500)
            max_n = 0
            for t in tasks:
                tid = (t.get("id") or "")
                if tid.startswith("GOV-"):
                    try:
                        max_n = max(max_n, int(tid[4:]))
                    except ValueError:
                        pass
            async with self._counter_lock:
                self._counter = max_n + 1
        except Exception:
            pass

    def _room(self, room_id: str) -> RoomState:
        if room_id not in self._rooms:
            self._rooms[room_id] = RoomState(room_id, self._bus)
        return self._rooms[room_id]

    async def _next_id(self) -> str:
        async with self._counter_lock:
            did = f"GOV-{self._counter:04d}"
            self._counter += 1
            return did

    def make_event(
        self,
        event_type: str,
        decision_id: str,
        room_id: str,
        actor: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a signed governance event."""
        nonce = uuid.uuid4().hex
        ts = _now_ms()
        ev: dict[str, Any] = {
            "event": event_type,
            "event_type": event_type,
            "event_id": uuid.uuid4().hex,
            "task_id": decision_id,
            "decision_id": decision_id,
            "room_id": room_id,
            "actor": actor,
            "nonce": nonce,
            "created_at_ms": ts,
            **(extra or {}),
        }
        ev["signature"] = _sign(ev)
        return ev

    async def create_decision(
        self,
        intent: str,
        actor: str,
        room_id: str = "governance",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Create GOV-XXXX — always the first thing that happens. task_id born here."""
        decision_id = await self._next_id()
        dispatch_plan = _build_dispatch_plan(intent)
        await self._append_routing_audit(
            "input",
            {
                "decision_id": decision_id,
                "room_id": room_id,
                "actor": actor,
                "intent": intent,
            },
        )
        # Persist in tasks table
        from models import TaskIn
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
        await self._append_routing_audit(
            "classifier",
            {
                "decision_id": decision_id,
                "source": dispatch_plan.source,
                "classification": dispatch_plan.classification,
                "mention_target": dispatch_plan.mention_target,
            },
        )
        await self._append_routing_audit(
            "route",
            {
                "decision_id": decision_id,
                "primary_agent": dispatch_plan.primary_agent,
                "response_agents": list(dispatch_plan.response_agents),
                "execution_targets": list(dispatch_plan.execution_targets),
                "mode": dispatch_plan.mode,
                "reason": dispatch_plan.reason,
            },
        )
        logger.info(
            "dispatch selected decision_id=%s room_id=%s source=%s classification=%s "
            "primary=%s responses=%s executors=%s mention=%s",
            decision_id,
            room_id,
            dispatch_plan.source,
            dispatch_plan.classification,
            dispatch_plan.primary_agent,
            ",".join(dispatch_plan.response_agents),
            ",".join(dispatch_plan.execution_targets) or "-",
            dispatch_plan.mention_target or "-",
        )
        # Emit decision_created — GOV-0001 is first
        ev = self.make_event(
            "decision_created", decision_id, room_id, actor,
            {
                "intent": intent,
                "context": context or {},
                "dispatch": dispatch_plan.to_metadata(),
            },
        )
        await self._bus.publish(ev)
        await self._bus.publish(self.make_event(
            "gov_route_selected", decision_id, room_id, actor,
            {"dispatch": dispatch_plan.to_metadata()},
        ))
        return decision_id

    def get_room_state(self, room_id: str) -> dict[str, Any]:
        r = self._room(room_id)
        return {
            "room_id": room_id,
            "typing_locked": r.typing_locked,
            "typing_actor": r.typing_actor,
            "response_mutex": r.response_mutex,
            "active_panel": r.active_panel,
        }

    async def set_typing(self, room_id: str, actor: str, is_typing: bool) -> None:
        room = self._room(room_id)
        await room.set_typing(actor, is_typing)
        ev = self.make_event(
            "gov_typing_start" if is_typing else "gov_typing_end",
            "GOV-TYPING", room_id, actor,
        )
        await self._bus.publish(ev)

    async def stream_responses(
        self,
        decision_id: str,
        intent: str,
        room_id: str,
        actor: str,
        archie_system: str = "",
        charlie_system: str = "",
    ) -> None:
        """Stream Archie first, then Charlie — sequential per room mutex."""
        asyncio.create_task(
            self._run_sequential(decision_id, intent, room_id, actor, archie_system, charlie_system)
        )

    async def _run_sequential(
        self,
        decision_id: str,
        intent: str,
        room_id: str,
        actor: str,
        archie_system: str,
        charlie_system: str,
    ) -> None:
        plan = _build_dispatch_plan(intent)

        if plan.response_agents == ("archie",):
            await self._stream_one_panel(
                "archie", decision_id, intent, room_id, actor,
                archie_system or ARCHIE_DEFAULT_SYSTEM,
                _archie_stream,
            )
            return

        # Charlie-only routes, including execution-spec translation.
        if plan.response_agents == ("charlie",):
            prompt = _build_dispatch_prompt(intent, plan) if plan.execution_targets else intent
            system = CHARLIE_DISPATCH_SYSTEM if plan.execution_targets else (charlie_system or CHARLIE_DEFAULT_SYSTEM)
            charlie_text = await self._stream_one_panel(
                "charlie", decision_id, prompt, room_id, actor,
                system,
                _charlie_stream,
            )
            if plan.execution_targets and charlie_text:
                await self._publish_dispatch_specs(decision_id, room_id, actor, plan, charlie_text)
            return

        # Archie-first governance routes.
        if plan.response_agents != ("archie", "charlie"):
            await self._stream_one_panel(
                "charlie", decision_id, intent, room_id, actor,
                charlie_system or CHARLIE_DEFAULT_SYSTEM,
                _charlie_stream,
            )
            return

        # Stream Archie first — capture full response so Charlie can read it.
        archie_text = await self._stream_one_panel(
            "archie", decision_id, intent, room_id, actor,
            archie_system or ARCHIE_DEFAULT_SYSTEM,
            _archie_stream,
        )
        # Charlie's prompt: Papa's intent + Archie's full analysis.
        # Charlie MUST see Archie's response before forming his own assessment.
        if archie_text:
            charlie_prompt = (
                f"Papa's intent: {intent}\n\n"
                f"Archie's analysis:\n{archie_text}\n\n"
                "Now give your assessment: SCOPE / RISK / GAPS / VERDICT."
            )
        else:
            charlie_prompt = intent
        await self._stream_one_panel(
            "charlie", decision_id, charlie_prompt, room_id, actor,
            charlie_system or CHARLIE_DEFAULT_SYSTEM,
            _charlie_stream,
        )

    async def _publish_dispatch_specs(
        self,
        decision_id: str,
        room_id: str,
        actor: str,
        plan: DispatchPlan,
        spec_text: str,
    ) -> None:
        """Emit one dispatch event per execution target with Charlie's translated spec."""
        if len(plan.execution_targets) > 1 and not all(
            f"=== {target.upper()} SPEC ===" in spec_text for target in plan.execution_targets
        ):
            await self._append_routing_audit(
                "split_fallback",
                {"decision_id": decision_id, "targets": list(plan.execution_targets)},
            )
        specs_by_target = _split_dispatch_specs(spec_text, plan.execution_targets)
        await self._append_routing_audit(
            "spec",
            {
                "decision_id": decision_id,
                "targets": list(plan.execution_targets),
                "specs_by_target": specs_by_target,
                "notify_agents": list(plan.notify_agents),
                "requires_executor_touch": plan.requires_executor_touch,
                "close_guard_targets": list(plan.close_guard_targets),
                "close_guard_policy": plan.close_guard_policy,
                "required_subagent_lanes": list(plan.required_subagent_lanes),
                "sweep_passes_required": plan.sweep_passes_required,
            },
        )
        if plan.requires_executor_touch:
            await self._append_routing_audit(
                "factory_coordination",
                {
                    "decision_id": decision_id,
                    "notify_agents": list(plan.notify_agents),
                    "execution_targets": list(plan.execution_targets),
                    "close_guard_targets": list(plan.close_guard_targets),
                    "close_guard_policy": plan.close_guard_policy,
                    "required_subagent_lanes": list(plan.required_subagent_lanes),
                    "sweep_passes_required": plan.sweep_passes_required,
                },
            )
        await self._bus.publish(self.make_event(
            "gov_dispatch_ready", decision_id, room_id, actor,
            {
                "dispatch": plan.to_metadata(),
                "spec": spec_text,
                "specs_by_target": specs_by_target,
                "coordination": {
                    "notify_agents": list(plan.notify_agents),
                    "requires_executor_touch": plan.requires_executor_touch,
                    "close_guard_targets": list(plan.close_guard_targets),
                    "close_guard_policy": plan.close_guard_policy,
                    "required_subagent_lanes": list(plan.required_subagent_lanes),
                    "sweep_passes_required": plan.sweep_passes_required,
                },
            },
        ))
        for target in plan.execution_targets:
            target_spec = specs_by_target.get(target, spec_text)
            await self._bus.publish(self.make_event(
                "gov_dispatch_target", decision_id, room_id, actor,
                {
                    "dispatch": plan.to_metadata(),
                    "target": target,
                    "spec": target_spec,
                },
            ))

    async def _stream_one_panel(
        self,
        panel: str,
        decision_id: str,
        intent: str,
        room_id: str,
        actor: str,
        system: str,
        stream_fn,
    ) -> str:
        """Stream one panel. Returns full collected response text."""
        room = self._room(room_id)
        # Acquire mutex — blocks if another panel is still streaming
        for _ in range(30):  # Wait up to 30s for mutex
            if await room.acquire_mutex(panel):
                break
            await asyncio.sleep(1)
        else:
            await self._bus.publish(self.make_event("gov_stream_timeout", decision_id, room_id, actor, {"panel": panel, "reason": "mutex_wait_timeout", "timeout_s": 30}))
            return ""  # Give up

        collected: list[str] = []
        try:
            await self._bus.publish(self.make_event(
                "gov_response_begin", decision_id, room_id, actor, {"panel": panel},
            ))
            seq = 0
            async for delta in stream_fn(intent, system):
                collected.append(delta)
                await self._bus.publish(self.make_event(
                    "gov_stream_delta", decision_id, room_id, actor,
                    {"panel": panel, "delta": delta, "seq": seq},
                ))
                seq += 1
            await self._bus.publish(self.make_event(
                "gov_response_end", decision_id, room_id, actor,
                {"panel": panel, "tokens": seq, "content": "".join(collected)},
            ))
        except Exception as exc:
            await self._bus.publish(self.make_event(
                "gov_stream_error", decision_id, room_id, actor,
                {"panel": panel, "error": str(exc)},
            ))
        finally:
            await room.release_mutex()
        return "".join(collected)


# ── LLM streaming generators ─────────────────────────────────────────────────

async def _openrouter_stream(prompt: str, system: str, model: str) -> AsyncIterator[str]:
    """Generic OpenRouter SSE streaming — yields text deltas."""
    import httpx
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1024,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://titanarray.net",
                "X-Title": "TitanArray Governance",
            },
            json=payload,
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                    delta = (data.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


async def _archie_stream(prompt: str, system: str) -> AsyncIterator[str]:
    async for t in _openrouter_stream(prompt, system, ARCHIE_GOV_MODEL):
        yield t


async def _alibaba_stream(prompt: str, system: str, model: str) -> AsyncIterator[str]:
    """Alibaba DashScope OpenAI-compatible SSE streaming."""
    import httpx
    import logging
    _log = logging.getLogger(__name__)
    url = ALIBABA_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1024,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", url,
            headers={
                "Authorization": f"Bearer {ALIBABA_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                _log.error(
                    "[alibaba_stream] Non-200 response: status=%s url=%s body=%s",
                    resp.status_code, url, body.decode(errors='replace')[:500],
                )
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk.strip() == "[DONE]":
                    break
                try:
                    import json as _json
                    data = _json.loads(chunk)
                    delta = (data.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
                except (ValueError, KeyError, IndexError) as e:
                    _log.warning("[alibaba_stream] Parse error on chunk %r: %s", chunk, e)
                    continue


async def _charlie_stream(prompt: str, system: str) -> AsyncIterator[str]:
    """Charlie via Alibaba DashScope; falls back to OpenRouter if Alibaba returns nothing.

    Fallback triggers when Alibaba returns 401, times out, or yields zero tokens.
    Fallback model: CHARLIE_OR_MODEL (default: qwen/qwen-2.5-72b-instruct via OpenRouter).
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    got_any = False
    async for t in _alibaba_stream(prompt, system, CHARLIE_GOV_MODEL):
        got_any = True
        yield t
    if not got_any:
        _log.warning(
            "[charlie_stream] Alibaba yielded nothing — falling back to OpenRouter (%s). "
            "Check ALIBABA_API_KEY validity.",
            CHARLIE_OR_MODEL,
        )
        async for t in _openrouter_stream(prompt, system, CHARLIE_OR_MODEL):
            yield t
