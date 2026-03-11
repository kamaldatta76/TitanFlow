from __future__ import annotations

from pathlib import Path

from titanocta.context import ContextInjector, ContextStore


def test_context_injector_caps_and_orders_by_relevance(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    store.add_entry(user_id="u1", session_id="s1", role="user", content="a", score=0.40, token_estimate=5)
    store.add_entry(user_id="u1", session_id="s1", role="assistant", content="b", score=0.95, token_estimate=7)
    store.add_entry(user_id="u1", session_id="s1", role="assistant", content="c", score=0.70, token_estimate=9)

    injector = ContextInjector(store, max_items=2, min_score=0.35)
    ctx = injector.inject(user_id="u1", session_id="s1")

    assert len(ctx.items) == 2
    assert [item.content for item in ctx.items] == ["b", "c"]
    assert ctx.token_total == 16


def test_context_injector_prompt_block_empty_when_no_hits(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    store.add_entry(user_id="u1", session_id="s1", role="user", content="x", score=0.10)
    injector = ContextInjector(store, max_items=5, min_score=0.8)
    ctx = injector.inject(user_id="u1", session_id="s1")
    assert ctx.as_prompt_block() == ""
