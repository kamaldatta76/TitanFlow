"""
Tier definitions and constellation defaults for TitanOcta.

OCTA AGENT LADDER (locked 2026-03-13 — Papa + CL + AC)
True to the name. Eight agents max, released in pairs.

  Tier        Agents  Price   LLM Mode
  ──────────────────────────────────────────────────────
  free           2    $0      Local only (or capped trial)
  tier1          4    $2/mo   Local + online pair
  tier2          6    $5/mo   Local + online pair
  ultra          8    $10/mo  Full constellation
  patreon        8    $15/mo  Full + Papa's safe public builds (one model behind)

BYO Key is a separate mode — not a tier. User brings their own API key,
unlocks the online lane without a subscription.

PULSE CHECK applies to all tiers equally. It is not a paid feature.

SINGLE USER ships first. Multi-user inherits the same doctrine — tiers,
pulse check, LLM pairing, community layer. Frozen until single-user is stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os

# ── Tier keys ─────────────────────────────────────────────────────────────────
TIER_FREE     = "free"
TIER_1        = "tier1"
TIER_2        = "tier2"
TIER_ULTRA    = "ultra"
TIER_PATREON  = "patreon"

# Legacy aliases — kept for backwards compat with existing callers
TIER_PRO      = "tier1"
TIER_MANAGER  = "ultra"
TIER_DASH_PACK = "ultra"

MANAGEMENT_HOST = os.environ.get("TITANOCTA_MANAGEMENT_HOST", "0.0.0.0")
MANAGEMENT_PORT = int(os.environ.get("TITANOCTA_MANAGEMENT_PORT", "8765"))


# ── LLM pairing modes ─────────────────────────────────────────────────────────
LLM_MODE_LOCAL   = "local"       # local only — free tier default
LLM_MODE_ONLINE  = "online"      # hosted online lane (paid tiers)
LLM_MODE_PAIR    = "pair"        # local + online running together (tier1+)
LLM_MODE_BYO     = "byo"         # user supplies their own API key

# Online LLM chosen for the hosted lane — can be overridden via env
ONLINE_LLM_DEFAULT = os.environ.get("TITANOCTA_ONLINE_LLM", "claude-haiku-3-5")

# Local LLM — hardware-dependent; user configures during onboarding
LOCAL_LLM_DEFAULT  = os.environ.get("TITANOCTA_LOCAL_LLM", "qwen2.5:7b")


@dataclass(frozen=True)
class ConstellationEndpoint:
    name: str
    url: str
    priority: int


@dataclass(frozen=True)
class TierLimits:
    max_users: int
    max_agents: int
    max_nodes: int


@dataclass(frozen=True)
class TierDefinition:
    key: str
    label: str
    price_monthly_usd: float
    limits: TierLimits
    llm_mode: str
    pulse_check: bool = True        # all tiers — not a paid feature
    byo_key_allowed: bool = True    # always allowed
    patreon_builds: bool = False    # Patreon only: Papa's safe public lane
    upcoming: bool = False
    description: str = ""


TIERS: dict[str, TierDefinition] = {
    # ── Free ──────────────────────────────────────────────────────────────────
    TIER_FREE: TierDefinition(
        key=TIER_FREE,
        label="Free",
        price_monthly_usd=0.0,
        limits=TierLimits(max_users=1, max_agents=2, max_nodes=1),
        llm_mode=LLM_MODE_LOCAL,
        description=(
            "2 agents. Local LLM only. Hardware-dependent. "
            "Online lane unlocked with BYO key or upgrade. "
            "Pulse check included."
        ),
    ),

    # ── Tier 1 — $2/mo ────────────────────────────────────────────────────────
    TIER_1: TierDefinition(
        key=TIER_1,
        label="Tier 1",
        price_monthly_usd=2.0,
        limits=TierLimits(max_users=1, max_agents=4, max_nodes=2),
        llm_mode=LLM_MODE_PAIR,
        description=(
            "4 agents. Local + online LLM pair. "
            "First paid expansion. Two more agents, both lanes live."
        ),
    ),

    # ── Tier 2 — $5/mo ────────────────────────────────────────────────────────
    TIER_2: TierDefinition(
        key=TIER_2,
        label="Tier 2",
        price_monthly_usd=5.0,
        limits=TierLimits(max_users=1, max_agents=6, max_nodes=4),
        llm_mode=LLM_MODE_PAIR,
        description=(
            "6 agents. Local + online LLM pair. "
            "Two more agents added to the constellation."
        ),
    ),

    # ── Ultra — $10/mo ────────────────────────────────────────────────────────
    TIER_ULTRA: TierDefinition(
        key=TIER_ULTRA,
        label="Ultra",
        price_monthly_usd=10.0,
        limits=TierLimits(max_users=1, max_agents=8, max_nodes=8),
        llm_mode=LLM_MODE_PAIR,
        description=(
            "Full 8-agent constellation. Full Octa. "
            "Local + online pair across all lanes."
        ),
    ),

    # ── Patreon — $15/mo ──────────────────────────────────────────────────────
    TIER_PATREON: TierDefinition(
        key=TIER_PATREON,
        label="Patreon",
        price_monthly_usd=15.0,
        limits=TierLimits(max_users=1, max_agents=8, max_nodes=8),
        llm_mode=LLM_MODE_PAIR,
        patreon_builds=True,
        description=(
            "Full 8-agent constellation + Papa's safe public builds. "
            "Always one model version behind Papa's active edge. "
            "Community-first release lane."
        ),
    ),
}

# ── Hardware class → recommended local LLM ────────────────────────────────────
HARDWARE_CLASS_MODEL_MAP: dict[str, str] = {
    "Full":             "qwen2.5:14b",    # Shadow/Shark class (9070XT / 4070)
    "Lite":             "qwen2.5:7b",     # mid-range GPU
    "Mini":             "qwen2.5:3b",     # low-RAM or CPU-only
    "Remote-assisted":  os.environ.get("TITANOCTA_REMOTE_ASSISTED_MODEL", "qwen2.5:7b"),
}

CONSTELLATION_ONBOARDING_HOSTS: tuple[ConstellationEndpoint, ...] = (
    ConstellationEndpoint(
        name="TitanThunder",
        url=os.environ.get(
            "TITANOCTA_THUNDER_REGISTER_URL",
            "https://thunder.titan.internal/api/register",
        ),
        priority=1,
    ),
    ConstellationEndpoint(
        name="Thor",
        url=os.environ.get(
            "TITANOCTA_THOR_REGISTER_URL",
            "https://thor.titan.internal/api/register",
        ),
        priority=2,
    ),
)

EVALUATOR_POOL: tuple[ConstellationEndpoint, ...] = (
    ConstellationEndpoint(
        name="TitanThunder",
        url=os.environ.get("TITANOCTA_THUNDER_EVAL_URL", "https://thunder.titan.internal"),
        priority=1,
    ),
    ConstellationEndpoint(
        name="Thor",
        url=os.environ.get("TITANOCTA_THOR_EVAL_URL", "https://thor.titan.internal"),
        priority=2,
    ),
)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_tier_definition(tier: str) -> TierDefinition:
    """Resolve a tier key to its definition. Raises ValueError for unknown keys."""
    # Normalise legacy keys
    normalised = {
        "pro": TIER_1,
        "manager": TIER_ULTRA,
        "dash_pack": TIER_ULTRA,
    }.get(tier, tier)
    try:
        return TIERS[normalised]
    except KeyError as exc:
        raise ValueError(f"Unknown TitanOcta tier: {tier!r}") from exc


def recommended_model_for_class(class_name: str) -> str:
    return HARDWARE_CLASS_MODEL_MAP.get(class_name, HARDWARE_CLASS_MODEL_MAP["Lite"])


def tier_allows_online_llm(tier: str, byo_key: bool = False) -> bool:
    """
    True if the tier has access to an online LLM lane.
    BYO key unlocks the online lane regardless of tier.
    Free tier is local-only unless BYO key is provided.
    """
    if byo_key:
        return True
    defn = get_tier_definition(tier)
    return defn.llm_mode in (LLM_MODE_ONLINE, LLM_MODE_PAIR)


def max_agents_for_tier(tier: str) -> int:
    return get_tier_definition(tier).limits.max_agents


def tier_summary() -> list[dict]:
    """Return the full tier ladder as a list — useful for the portal and onboarding UI."""
    return [
        {
            "key": t.key,
            "label": t.label,
            "price_monthly_usd": t.price_monthly_usd,
            "max_agents": t.limits.max_agents,
            "llm_mode": t.llm_mode,
            "pulse_check": t.pulse_check,
            "patreon_builds": t.patreon_builds,
            "description": t.description,
        }
        for t in TIERS.values()
    ]
