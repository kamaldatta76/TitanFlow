"""mem0-style long-term memory client for TitanFlow.

Uses Qdrant REST API + Ollama embed/generate to:
  - Extract memorable facts from conversation turns
  - Embed and store them in Qdrant
  - Recall relevant memories given a query
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("titanflow.mem0")

# ── Defaults ──────────────────────────────────────────────────
QDRANT_URL = "http://10.0.0.32:6333"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EXTRACT_MODEL = "cogito:14b"
COLLECTION = "titanflow_memories"
VECTOR_SIZE = 768  # nomic-embed-text
TOP_K = 5
SCORE_THRESHOLD = 0.35

EXTRACT_PROMPT = """You are a memory extraction engine. Given a user message and assistant response, extract the key facts worth remembering for future conversations. Focus on:
- User preferences, opinions, and interests
- Personal facts (names, locations, relationships, work)
- Technical decisions or configurations mentioned
- Important events or dates
- Requests or goals the user expressed

Output ONLY a JSON array of short fact strings. If nothing memorable, output [].

User: {user_msg}
Assistant: {assist_msg}

Facts (JSON array):"""


class Mem0Client:
    """Lightweight mem0-style client using Qdrant + Ollama."""

    def __init__(
        self,
        *,
        qdrant_url: str = QDRANT_URL,
        ollama_url: str = OLLAMA_URL,
        embed_model: str = EMBED_MODEL,
        extract_model: str = EXTRACT_MODEL,
        collection: str = COLLECTION,
        top_k: int = TOP_K,
    ):
        self.qdrant_url = qdrant_url.rstrip("/")
        self.ollama_url = ollama_url.rstrip("/")
        self.embed_model = embed_model
        self.extract_model = extract_model
        self.collection = collection
        self.top_k = top_k
        self._http = httpx.AsyncClient(timeout=60.0)
        self._collection_ready = False

    async def close(self) -> None:
        await self._http.aclose()

    # ── Qdrant helpers ─────────────────────────────────────────

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        try:
            r = await self._http.get(f"{self.qdrant_url}/collections/{self.collection}")
            if r.status_code == 200:
                self._collection_ready = True
                return
            # Create
            r = await self._http.put(
                f"{self.qdrant_url}/collections/{self.collection}",
                json={
                    "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
                    "on_disk_payload": True,
                },
            )
            r.raise_for_status()
            self._collection_ready = True
            logger.info("Created Qdrant collection: %s", self.collection)
        except Exception as exc:
            logger.warning("Qdrant collection check failed: %s", exc)

    async def _store_point(self, fact: str, vector: list[float], meta: dict[str, Any]) -> None:
        point_id = str(uuid.uuid4())
        r = await self._http.put(
            f"{self.qdrant_url}/collections/{self.collection}/points",
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": {
                            "text": fact,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            **meta,
                        },
                    }
                ]
            },
        )
        r.raise_for_status()

    async def _search(self, vector: list[float], limit: int) -> list[str]:
        r = await self._http.post(
            f"{self.qdrant_url}/collections/{self.collection}/points/search",
            json={
                "vector": vector,
                "limit": limit,
                "with_payload": True,
                "score_threshold": SCORE_THRESHOLD,
            },
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            hit["payload"]["text"]
            for hit in data.get("result", [])
            if hit.get("payload", {}).get("text")
        ]

    # ── Ollama helpers ─────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        r = await self._http.post(
            f"{self.ollama_url}/api/embed",
            json={"model": self.embed_model, "input": text},
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]

    async def _extract_facts(self, user_msg: str, assist_msg: str) -> list[str]:
        prompt = EXTRACT_PROMPT.format(user_msg=user_msg[:500], assist_msg=assist_msg[:500])
        r = await self._http.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.extract_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 1024, "temperature": 0.1},
            },
        )
        r.raise_for_status()
        raw = r.json().get("response", "").strip()

        # Parse JSON array
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        try:
            arr = json.loads(cleaned)
            if isinstance(arr, list):
                return [s for s in arr if isinstance(s, str) and len(s) > 5]
        except json.JSONDecodeError:
            import re
            m = re.search(r"\[[\s\S]*?\]", cleaned)
            if m:
                try:
                    arr = json.loads(m.group())
                    if isinstance(arr, list):
                        return [s for s in arr if isinstance(s, str) and len(s) > 5]
                except json.JSONDecodeError:
                    pass
        return []

    # ── Public API ─────────────────────────────────────────────

    async def recall(self, query: str, limit: int | None = None) -> list[str]:
        """Recall memories relevant to *query*."""
        try:
            await self._ensure_collection()
            vec = await self._embed(query)
            return await self._search(vec, limit or self.top_k)
        except Exception as exc:
            logger.debug("mem0 recall error: %s", exc)
            return []

    async def capture(self, user_msg: str, assist_msg: str) -> int:
        """Extract and store memorable facts from a conversation turn.

        Returns the number of facts stored.
        """
        if len(user_msg) < 10 or user_msg.startswith("/"):
            return 0
        try:
            await self._ensure_collection()
            facts = await self._extract_facts(user_msg, assist_msg)
            if not facts:
                return 0

            stored = 0
            for fact in facts:
                try:
                    vec = await self._embed(fact)
                    await self._store_point(fact, vec, {
                        "source": "conversation",
                        "user_preview": user_msg[:200],
                    })
                    stored += 1
                except Exception as exc:
                    logger.debug("mem0 store error for '%s': %s", fact[:40], exc)
            logger.info("mem0: stored %d/%d facts", stored, len(facts))
            return stored
        except Exception as exc:
            logger.debug("mem0 capture error: %s", exc)
            return 0

    async def store_fact(self, fact: str, source: str = "manual") -> bool:
        """Store a single fact directly."""
        try:
            await self._ensure_collection()
            vec = await self._embed(fact)
            await self._store_point(fact, vec, {"source": source})
            return True
        except Exception as exc:
            logger.debug("mem0 store_fact error: %s", exc)
            return False
