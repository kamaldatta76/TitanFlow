"""TitanOcta routing wrapper on top of the governance engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any

from ._octopus import governance_module
from .relay_policy import (
    RELAY_STAGE_BUILDER_RESPONSE,
    RELAY_STAGE_DISPATCHED,
    RELAY_STAGE_RECEIVED,
    RELAY_STAGE_SYNTHESIZED_UPSTREAM,
    RELAY_STAGE_TRANSLATED,
    RELAY_STAGE_VERIFIED,
    RelayPolicy,
    load_relay_policy,
    parse_translation_spec,
    validate_record,
)


@dataclass(frozen=True)
class TitanOctaRoute:
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TitanOctaRouter:
    def __init__(
        self,
        *,
        audit_log_path: str = "~/.titanocta/routing-audit.jsonl",
        relay_policy_path: str | None = None,
    ) -> None:
        self._audit_log_path = Path(audit_log_path).expanduser()
        self._builder = governance_module()._build_dispatch_plan
        self._relay_policy: RelayPolicy = load_relay_policy(relay_policy_path)

    def route(self, intent: str) -> TitanOctaRoute:
        plan = self._builder(intent)
        route = TitanOctaRoute(
            source=plan.source,
            classification=plan.classification,
            primary_agent=plan.primary_agent,
            response_agents=tuple(plan.response_agents),
            execution_targets=tuple(plan.execution_targets),
            mode=plan.mode,
            reason=plan.reason,
            mention_target=plan.mention_target,
            notify_agents=tuple(getattr(plan, "notify_agents", ())),
            requires_executor_touch=bool(getattr(plan, "requires_executor_touch", False)),
            close_guard_targets=tuple(getattr(plan, "close_guard_targets", ())),
            close_guard_policy=str(getattr(plan, "close_guard_policy", "none")),
            required_subagent_lanes=tuple(getattr(plan, "required_subagent_lanes", ())),
            sweep_passes_required=int(getattr(plan, "sweep_passes_required", 1)),
        )
        self._append_audit(
            {
                "stage": RELAY_STAGE_RECEIVED,
                "intent": intent,
                "relay_policy": {
                    "name": self._relay_policy.name,
                    "version": self._relay_policy.version,
                    "required_order": list(self._relay_policy.required_stages),
                    "sequence_downstream": list(self._relay_policy.sequence_downstream),
                    "sequence_upstream": list(self._relay_policy.sequence_upstream),
                },
                "route": route.to_dict(),
            }
        )
        self._append_audit(
            {
                "stage": "route",
                "intent": intent,
                "route": route.to_dict(),
            }
        )
        return route

    def append_spec_audit(self, intent: str, route: TitanOctaRoute, spec: str) -> None:
        parsed = parse_translation_spec(spec)
        is_valid, missing = validate_record(parsed, self._relay_policy.translation_spec_required)
        translation_payload = {
            "stage": RELAY_STAGE_TRANSLATED,
            "intent": intent,
            "route": route.to_dict(),
            "translation": parsed,
            "translation_contract": list(self._relay_policy.translation_spec_required),
            "translation_valid": is_valid,
            "translation_missing": list(missing),
        }
        self._append_audit(translation_payload)
        if route.execution_targets:
            for target in route.execution_targets:
                self._append_audit(
                    {
                        "stage": RELAY_STAGE_DISPATCHED,
                        "intent": intent,
                        "route": route.to_dict(),
                        "target": target,
                    }
                )
        self._append_audit(
            {
                "stage": "spec",
                "intent": intent,
                "route": route.to_dict(),
                "spec": spec,
            }
        )

    def append_builder_response_audit(
        self,
        *,
        decision_id: str | None,
        target: str,
        result: str,
        status: str = "completed",
    ) -> None:
        self._append_audit(
            {
                "stage": RELAY_STAGE_BUILDER_RESPONSE,
                "decision_id": decision_id,
                "target": target,
                "status": status,
                "result": result,
            }
        )

    def append_verification_audit(
        self,
        *,
        decision_id: str | None,
        verification_record: dict[str, Any],
    ) -> None:
        is_valid, missing = validate_record(
            verification_record,
            self._relay_policy.verification_record_required,
        )
        self._append_audit(
            {
                "stage": RELAY_STAGE_VERIFIED,
                "decision_id": decision_id,
                "verification_record": verification_record,
                "verification_contract": list(self._relay_policy.verification_record_required),
                "verification_valid": is_valid,
                "verification_missing": list(missing),
            }
        )

    def append_synthesized_audit(
        self,
        *,
        decision_id: str | None,
        actor: str,
        response: str,
        upstream_target: str = "papa_or_ac",
    ) -> None:
        self._append_audit(
            {
                "stage": RELAY_STAGE_SYNTHESIZED_UPSTREAM,
                "decision_id": decision_id,
                "actor": actor,
                "upstream_target": upstream_target,
                "response": response,
            }
        )

    def _append_audit(self, payload: dict[str, Any]) -> None:
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts_ms": int(time.time() * 1000), **payload}
        with self._audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
