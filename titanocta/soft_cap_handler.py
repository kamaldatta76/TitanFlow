"""Soft-cap strategy resolver."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SoftCapDecision:
    strategy: str | None
    action: str
    note: str


def apply_soft_cap_strategy(
    strategy: str | None,
    *,
    redirect_to_thor_enabled: bool = False,
) -> SoftCapDecision:
    if strategy in (None, ""):
        return SoftCapDecision(strategy=None, action="none", note="no_soft_cap_strategy")
    if strategy == "cheapest_allowed":
        return SoftCapDecision(strategy=strategy, action="cheapest_allowed", note="route_to_lowest_cost_model")
    if strategy == "redirect_to_thor":
        if redirect_to_thor_enabled:
            return SoftCapDecision(
                strategy=strategy,
                action="redirect_to_thor_pending",
                note="flag accepted; redirect behavior intentionally dormant in Phase A",
            )
        return SoftCapDecision(
            strategy=strategy,
            action="none",
            note="redirect_to_thor requested but disabled for this user",
        )
    if strategy == "deny":
        return SoftCapDecision(strategy=strategy, action="deny", note="hard deny requested by strategy")
    return SoftCapDecision(strategy=strategy, action="none", note="unknown strategy ignored")
