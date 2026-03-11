"""Builds scoped context windows for model calls."""

from __future__ import annotations

from dataclasses import dataclass

from .context_store import ContextEntry, ContextStore


@dataclass(frozen=True)
class InjectedContext:
    items: tuple[ContextEntry, ...]
    token_total: int
    min_score: float
    max_items: int

    def as_prompt_block(self) -> str:
        if not self.items:
            return ""
        lines = ["Context memory (most relevant first):"]
        for item in self.items:
            lines.append(f"- [{item.role}] {item.content}")
        return "\n".join(lines)


class ContextInjector:
    def __init__(
        self,
        store: ContextStore,
        *,
        max_items: int = 5,
        min_score: float = 0.35,
    ) -> None:
        self._store = store
        self._max_items = max_items
        self._min_score = min_score

    def inject(self, *, user_id: str, session_id: str) -> InjectedContext:
        entries = self._store.list_entries(
            user_id=user_id,
            session_id=session_id,
            min_score=self._min_score,
            limit=200,
        )
        ranked = sorted(entries, key=lambda e: (e.score, e.id), reverse=True)[: self._max_items]
        token_total = sum(e.token_estimate for e in ranked)
        return InjectedContext(
            items=tuple(ranked),
            token_total=token_total,
            min_score=self._min_score,
            max_items=self._max_items,
        )
