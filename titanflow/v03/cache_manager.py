"""Cache manager wrapper for scheduled eviction."""

from __future__ import annotations

from titanflow.v03.llm_broker import LLMBroker


class CacheManager:
    def __init__(self, broker: LLMBroker) -> None:
        self._broker = broker

    async def evict(self) -> None:
        await self._broker.evict_cache()
