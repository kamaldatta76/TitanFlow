"""Soft-threshold context flush into durable facts."""

from __future__ import annotations

from dataclasses import dataclass

from .context_store import ContextStore

_DURABLE_HINTS = (
    "must",
    "never",
    "always",
    "endpoint",
    "domain",
    "node",
    "tier",
    "model",
    "policy",
    "route",
    "credential",
    "token",
)


@dataclass(frozen=True)
class FlushResult:
    flushed: bool
    total_tokens: int
    added_durable_facts: int
    inspected_entries: int


class MemoryFlusher:
    def __init__(
        self,
        store: ContextStore,
        *,
        soft_token_limit: int = 1200,
        min_score: float = 0.35,
    ) -> None:
        self._store = store
        self._soft_token_limit = soft_token_limit
        self._min_score = min_score

    def flush_if_needed(self, *, user_id: str, session_id: str) -> FlushResult:
        total = self._store.total_tokens(user_id=user_id, session_id=session_id)
        if total < self._soft_token_limit:
            return FlushResult(flushed=False, total_tokens=total, added_durable_facts=0, inspected_entries=0)

        entries = self._store.list_entries(
            user_id=user_id,
            session_id=session_id,
            min_score=self._min_score,
            limit=30,
            exclude_kind="durable_fact",
        )
        durable_count = 0
        for entry in entries:
            fact = self._extract_durable_fact(entry.content)
            if not fact:
                continue
            if not self._store.durable_fact_exists(user_id=user_id, session_id=session_id, content=fact):
                self._store.add_entry(
                    user_id=user_id,
                    session_id=session_id,
                    role="system",
                    content=fact,
                    score=max(0.6, entry.score),
                    durable=True,
                    kind="durable_fact",
                )
                durable_count += 1
            # Prevent accumulation: source entries that yielded durable facts are demoted.
            self._store.update_score(entry_id=entry.id, score=0.0)

        return FlushResult(
            flushed=True,
            total_tokens=total,
            added_durable_facts=durable_count,
            inspected_entries=len(entries),
        )

    @staticmethod
    def _extract_durable_fact(text: str) -> str | None:
        compact = " ".join(text.split())
        lower = compact.lower()
        if any(hint in lower for hint in _DURABLE_HINTS):
            return compact[:400]
        return None
