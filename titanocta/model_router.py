"""Model routing + provider restrictions for TitanOcta tiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .credits.credit_events import EVENT_PROVIDER_EXCLUDED_ROUTE_BLOCKED
from .soft_cap_handler import SoftCapDecision, apply_soft_cap_strategy

_WESTERN_BLOCKED_MODELS = {"minimax-m2.5", "qwen-flash"}
_STRICT_FILTER_TERMS = {
    "porn",
    "explicit sex",
    "sexual content",
    "self-harm instructions",
    "suicide method",
}
_COST_ORDER = (
    "qwen-flash",
    "minimax-m2.5",
    "gpt-5-nano",
    "gpt-5-mini",
    "claude-haiku-4-5",
    "ollama-local",
)


@dataclass(frozen=True)
class RoutingProfile:
    available_models: tuple[str, ...]
    excluded_models: tuple[str, ...]
    provider_mode: str
    soft_cap_strategy: str | None
    warning_thresholds: tuple[float, ...]
    hard_cap: bool
    redirect_to_thor: bool = False
    content_filter: str | None = None


@dataclass(frozen=True)
class ModelRouteDecision:
    allowed: bool
    model: str
    reason: str
    provider_mode: str
    blocked_event: str | None = None
    soft_cap: SoftCapDecision | None = None


class ModelRouter:
    def __init__(self, *, audit_event: Callable[[str, dict[str, object]], None] | None = None) -> None:
        self._audit_event = audit_event

    def route_model(
        self,
        *,
        preferred_model: str,
        profile: RoutingProfile,
        prompt: str,
        soft_cap_engaged: bool = False,
    ) -> ModelRouteDecision:
        if profile.content_filter == "strict" and self._violates_strict_filter(prompt):
            return ModelRouteDecision(
                allowed=False,
                model=preferred_model,
                reason="Request blocked by strict content filter.",
                provider_mode=profile.provider_mode,
            )

        blocked_event: str | None = None
        selected = self._coerce_available(preferred_model, profile.available_models)
        if selected in profile.excluded_models:
            self._emit_provider_excluded(selected, profile)
            blocked_event = EVENT_PROVIDER_EXCLUDED_ROUTE_BLOCKED
            selected = self._fallback_model(profile, blocked_model=selected)

        if profile.provider_mode == "western_only" and selected in _WESTERN_BLOCKED_MODELS:
            self._emit_provider_excluded(selected, profile)
            blocked_event = EVENT_PROVIDER_EXCLUDED_ROUTE_BLOCKED
            selected = self._fallback_model(profile, blocked_model=selected)

        soft_cap_decision = None
        if soft_cap_engaged:
            soft_cap_decision = apply_soft_cap_strategy(
                profile.soft_cap_strategy,
                redirect_to_thor_enabled=profile.redirect_to_thor,
            )
            if soft_cap_decision.action == "deny":
                return ModelRouteDecision(
                    allowed=False,
                    model=selected,
                    reason="Credits exhausted. Strategy set to deny further requests.",
                    provider_mode=profile.provider_mode,
                    soft_cap=soft_cap_decision,
                )
            if soft_cap_decision.action == "cheapest_allowed":
                selected = self._cheapest_allowed(profile)

        return ModelRouteDecision(
            allowed=True,
            model=selected,
            reason="model_routed",
            provider_mode=profile.provider_mode,
            blocked_event=blocked_event,
            soft_cap=soft_cap_decision,
        )

    @staticmethod
    def _coerce_available(preferred_model: str, available_models: tuple[str, ...]) -> str:
        if not available_models:
            return preferred_model
        if "all" in available_models or "all-byok" in available_models:
            return preferred_model
        if preferred_model in available_models:
            return preferred_model
        return available_models[0]

    def _fallback_model(self, profile: RoutingProfile, *, blocked_model: str) -> str:
        available = list(profile.available_models)
        if "all" in available or "all-byok" in available:
            available = list(_COST_ORDER)
        excluded = set(profile.excluded_models)
        if profile.provider_mode == "western_only":
            excluded.update(_WESTERN_BLOCKED_MODELS)
        candidates = [m for m in _COST_ORDER if m in available and m not in excluded and m != blocked_model]
        if candidates:
            return candidates[0]
        safe_default = "ollama-local"
        if safe_default not in excluded:
            return safe_default
        return blocked_model

    def _cheapest_allowed(self, profile: RoutingProfile) -> str:
        available = list(profile.available_models)
        if "all" in available or "all-byok" in available:
            available = list(_COST_ORDER)
        excluded = set(profile.excluded_models)
        if profile.provider_mode == "western_only":
            excluded.update(_WESTERN_BLOCKED_MODELS)
        for model in _COST_ORDER:
            if model in available and model not in excluded:
                return model
        return "ollama-local"

    def _emit_provider_excluded(self, model: str, profile: RoutingProfile) -> None:
        if self._audit_event is None:
            return
        self._audit_event(
            EVENT_PROVIDER_EXCLUDED_ROUTE_BLOCKED,
            {
                "model": model,
                "provider_mode": profile.provider_mode,
            },
        )

    @staticmethod
    def _violates_strict_filter(prompt: str) -> bool:
        text = prompt.lower()
        return any(term in text for term in _STRICT_FILTER_TERMS)
