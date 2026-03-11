from __future__ import annotations

from pathlib import Path

from titanocta.context import ContextStore


def test_context_store_uses_wal_and_filters_by_score(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    assert store.journal_mode() == "wal"

    store.add_entry(user_id="u1", session_id="s1", role="user", content="low", score=0.2)
    store.add_entry(user_id="u1", session_id="s1", role="user", content="high", score=0.9)

    entries = store.list_entries(user_id="u1", session_id="s1", min_score=0.35, limit=10)
    assert len(entries) == 1
    assert entries[0].content == "high"


def test_context_store_token_total_scoped(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    store.add_entry(user_id="u1", session_id="s1", role="user", content="a" * 40, token_estimate=10)
    store.add_entry(user_id="u1", session_id="s1", role="assistant", content="b" * 80, token_estimate=20)
    store.add_entry(user_id="u1", session_id="s2", role="assistant", content="c" * 80, token_estimate=50)

    assert store.total_tokens(user_id="u1", session_id="s1") == 30
