from __future__ import annotations

from pathlib import Path

from titanocta.context import ContextStore, MemoryFlusher


def test_memory_flush_adds_durable_facts_when_over_threshold(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    store.add_entry(
        user_id="u1",
        session_id="s1",
        role="assistant",
        content="Policy: model route must stay on western providers only.",
        score=0.9,
        token_estimate=700,
    )
    store.add_entry(
        user_id="u1",
        session_id="s1",
        role="assistant",
        content="Endpoint for health is /health and node route policy is strict.",
        score=0.8,
        token_estimate=700,
    )
    flusher = MemoryFlusher(store, soft_token_limit=1000, min_score=0.35)
    result = flusher.flush_if_needed(user_id="u1", session_id="s1")
    assert result.flushed is True
    assert result.added_durable_facts >= 1

    durable = store.list_entries(user_id="u1", session_id="s1", only_durable=True, limit=20)
    assert durable
    assert all(item.kind == "durable_fact" for item in durable)

    # Source rows that produced durable facts are score-zeroed to avoid sludge.
    active = store.list_entries(user_id="u1", session_id="s1", min_score=0.35, limit=20)
    assert all(item.kind == "durable_fact" for item in active)


def test_memory_flush_does_not_duplicate_existing_durable_facts(tmp_path: Path) -> None:
    store = ContextStore(tmp_path / "context.sqlite")
    content = "Policy must route through approved nodes only."
    store.add_entry(
        user_id="u1",
        session_id="s1",
        role="assistant",
        content=content,
        score=0.9,
        token_estimate=1300,
    )
    flusher = MemoryFlusher(store, soft_token_limit=1000, min_score=0.35)
    first = flusher.flush_if_needed(user_id="u1", session_id="s1")
    second = flusher.flush_if_needed(user_id="u1", session_id="s1")

    durable = store.list_entries(user_id="u1", session_id="s1", only_durable=True, limit=20)
    assert first.added_durable_facts == 1
    assert second.added_durable_facts == 0
    assert len(durable) == 1
