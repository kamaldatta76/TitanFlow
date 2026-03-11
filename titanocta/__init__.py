"""TitanOcta Free v1.0 product surface on top of the Flow spine."""

from .agent import TitanOctaAgentRuntime
from .backup import BackupManager
from .context import ContextInjector, ContextStore, MemoryFlusher
from .credits import CreditMiddleware
from .config import TIER_FREE, TIER_MANAGER, TIER_PRO, TIER_ULTRA, TierDefinition, TierLimits
from .hardware import HardwareProfile, detect_hardware
from .model_router import ModelRouter, RoutingProfile
from .remote_token import RemoteAttachTokenManager
from .relay_policy import RelayPolicy, load_relay_policy
from .retrieval import GroundedRetriever, GroundingResult
from .routing import TitanOctaRouter
from .tai import TAi
from .tier_guard import TierGuard
from .evaluator import EvaluatorClient

__all__ = [
    "BackupManager",
    "ContextInjector",
    "ContextStore",
    "CreditMiddleware",
    "GroundedRetriever",
    "GroundingResult",
    "HardwareProfile",
    "RelayPolicy",
    "RemoteAttachTokenManager",
    "TIER_FREE",
    "TIER_MANAGER",
    "TIER_PRO",
    "TIER_ULTRA",
    "TierDefinition",
    "TierGuard",
    "TierLimits",
    "TitanOctaAgentRuntime",
    "TitanOctaRouter",
    "TAi",
    "EvaluatorClient",
    "ModelRouter",
    "RoutingProfile",
    "MemoryFlusher",
    "load_relay_policy",
    "detect_hardware",
]


def __getattr__(name: str):
    if name == "TitanOctaInstaller":
        from .installer import TitanOctaInstaller

        return TitanOctaInstaller
    raise AttributeError(name)
