from __future__ import annotations

from datetime import datetime, timezone

import pytest

from titanflow.core.config import DatabaseSettings
from titanflow.core.database_broker import DatabaseBroker


@pytest.mark.asyncio
async def test_database_broker_search_and_messages(tmp_path):
    db_path = tmp_path / "tf.db"
    db = DatabaseBroker(DatabaseSettings(path=str(db_path)))
    await db.init_schema()

    feed_source_id = await db.insert(
        "feed_sources",
        {
            "url": "https://example.com/rss",
            "name": "Example",
            "category": "general",
            "enabled": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    await db.insert(
        "feed_items",
        {
            "feed_source_id": feed_source_id,
            "guid": "example-1",
            "title": "GMS announces release",
            "url": "https://example.com/gms",
            "author": "",
            "content": "GMS is a fictional acronym used for testing.",
            "category": "general",
            "summary": "GMS is a test acronym.",
            "relevance_score": 0.9,
            "published_at": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "is_processed": 1,
            "is_published": 0,
        },
    )

    hits = await db.search("Who is GMS?", limit=6)
    assert hits
    assert any(hit["source_table"] == "feed_items" for hit in hits)

    await db.upsert_conversation("chat-1", user_id=123, role="user")
    await db.insert_message("chat-1", "user", "Hello there", token_est=3)
    await db.insert_message("chat-1", "assistant", "Hi!", token_est=1)

    messages = await db.fetch_messages("chat-1", limit=10)
    assert [m["role"] for m in messages] == ["user", "assistant"]

    await db.close()
