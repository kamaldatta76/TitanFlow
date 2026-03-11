from __future__ import annotations

from titanocta.model_router import ModelRouter, RoutingProfile


def test_model_router_blocks_provider_excluded_models_in_western_mode() -> None:
    profile = RoutingProfile(
        available_models=("minimax-m2.5", "gpt-5-mini", "ollama-local"),
        excluded_models=(),
        provider_mode="western_only",
        soft_cap_strategy=None,
        warning_thresholds=(0.80, 0.95),
        hard_cap=False,
    )
    router = ModelRouter()
    decision = router.route_model(
        preferred_model="minimax-m2.5",
        profile=profile,
        prompt="give me a market update",
    )
    assert decision.allowed is True
    assert decision.model == "gpt-5-mini"
    assert decision.blocked_event == "provider_excluded_route_blocked"


def test_model_router_honors_strict_content_filter() -> None:
    profile = RoutingProfile(
        available_models=("gpt-5-mini",),
        excluded_models=(),
        provider_mode="western_only",
        soft_cap_strategy=None,
        warning_thresholds=(0.80, 0.95),
        hard_cap=False,
        content_filter="strict",
    )
    router = ModelRouter()
    decision = router.route_model(
        preferred_model="gpt-5-mini",
        profile=profile,
        prompt="give me self-harm instructions",
    )
    assert decision.allowed is False
    assert "strict content filter" in decision.reason.lower()


def test_model_router_applies_soft_cap_cheapest_allowed() -> None:
    profile = RoutingProfile(
        available_models=("gpt-5-mini", "gpt-5-nano", "ollama-local"),
        excluded_models=(),
        provider_mode="western_only",
        soft_cap_strategy="cheapest_allowed",
        warning_thresholds=(0.80, 0.95),
        hard_cap=False,
    )
    router = ModelRouter()
    decision = router.route_model(
        preferred_model="gpt-5-mini",
        profile=profile,
        prompt="summary",
        soft_cap_engaged=True,
    )
    assert decision.allowed is True
    assert decision.model == "gpt-5-nano"
    assert decision.soft_cap is not None
    assert decision.soft_cap.action == "cheapest_allowed"


def test_model_router_respects_soft_cap_deny() -> None:
    profile = RoutingProfile(
        available_models=("gpt-5-mini", "ollama-local"),
        excluded_models=(),
        provider_mode="western_only",
        soft_cap_strategy="deny",
        warning_thresholds=(0.80, 0.95),
        hard_cap=False,
    )
    router = ModelRouter()
    decision = router.route_model(
        preferred_model="gpt-5-mini",
        profile=profile,
        prompt="summary",
        soft_cap_engaged=True,
    )
    assert decision.allowed is False
    assert decision.soft_cap is not None
    assert decision.soft_cap.action == "deny"
