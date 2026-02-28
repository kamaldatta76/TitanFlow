"""TitanFlow v0.2 Research Module — IPC-based."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover - optional dependency for runtime
    feedparser = None
import json
import yaml

from titanflow.modules.base_ipc import ModuleBaseIPC

logger = logging.getLogger("titanflow.research.ipc")

RESEARCH_SYSTEM_PROMPT = """You are TitanFlow's research analyst. Your job is to evaluate and summarize
technical news about LLMs, AI infrastructure, and developer tools.

When summarizing a feed item:
1. Write a concise 2-3 sentence summary focused on what's new and why it matters
2. Rate relevance from 0.0 to 1.0 based on:
   - 0.9-1.0: Major model release, breakthrough, or critical tool update
   - 0.7-0.8: Notable release, significant benchmark, industry shift
   - 0.5-0.6: Interesting but incremental update
   - 0.3-0.4: Tangentially related or minor
   - 0.0-0.2: Not relevant to LLM/AI infrastructure

Respond in this exact format:
SUMMARY: <your summary>
RELEVANCE: <score>"""


class ResearchModule(ModuleBaseIPC):
    def __init__(self) -> None:
        super().__init__()
        self.fetch_interval = int(os.environ.get("RESEARCH_FETCH_INTERVAL", "7200"))
        self.processing_batch_size = int(os.environ.get("RESEARCH_BATCH_SIZE", "50"))
        self.config_dir = Path(os.environ.get("TITANFLOW_CONFIG_DIR", "/opt/titanflow/config"))

    async def run(self) -> None:
        logger.info("Research module started (IPC)")
        asyncio.create_task(self._loop_fetch())
        asyncio.create_task(self._loop_process())
        await asyncio.Event().wait()

    async def _loop_fetch(self) -> None:
        while True:
            try:
                await self.fetch_all_feeds()
                await self.fetch_github_releases()
            except Exception as e:
                logger.warning("Fetch loop error: %s", e)
            await asyncio.sleep(self.fetch_interval)

    async def _loop_process(self) -> None:
        while True:
            try:
                await self.process_unprocessed()
            except Exception as e:
                logger.warning("Process loop error: %s", e)
            await asyncio.sleep(600)

    async def fetch_all_feeds(self) -> None:
        sources = await self._get_feed_sources()
        if not sources:
            await self._load_feeds_from_config()
            sources = await self._get_feed_sources()

        new_items = 0
        for source in sources:
            new_items += await self._fetch_feed(source)

        logger.info("Feed fetch complete — %d new items", new_items)

    async def _get_feed_sources(self) -> list[dict[str, Any]]:
        rows = await self.db_query(
            "feed_sources",
            "SELECT id, url, name, category FROM feed_sources WHERE enabled = 1",
        )
        return rows

    async def _load_feeds_from_config(self) -> None:
        feeds_path = self.config_dir / "feeds.yaml"
        if not feeds_path.exists():
            logger.warning("No feeds config at %s", feeds_path)
            return

        data = yaml.safe_load(feeds_path.read_text()) or {}
        for section_name, feeds in data.get("feeds", {}).items():
            for feed_def in feeds:
                url = feed_def["url"]
                existing = await self.db_query(
                    "feed_sources",
                    "SELECT id FROM feed_sources WHERE url = ?",
                    [url],
                )
                if existing:
                    continue
                await self.db_insert(
                    "feed_sources",
                    {
                        "url": url,
                        "name": feed_def.get("name", section_name),
                        "category": feed_def.get("category", "general"),
                        "enabled": 1,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

    async def _fetch_feed(self, source: dict[str, Any]) -> int:
        if feedparser is None:
            logger.warning("feedparser not installed; skipping feed %s", source.get("url"))
            return 0
        try:
            response = await self.http_request(source["url"], "GET")
            feed = feedparser.parse(response.get("body", ""))
        except Exception as e:
            logger.warning("Failed to fetch feed %s: %s", source["url"], e)
            return 0

        new_count = 0
        for entry in feed.entries[:50]:
            guid = entry.get("id") or entry.get("link") or hashlib.md5(
                entry.get("title", "").encode()
            ).hexdigest()
            existing = await self.db_query(
                "feed_items",
                "SELECT id FROM feed_items WHERE guid = ?",
                [guid],
            )
            if existing:
                continue

            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            await self.db_insert(
                "feed_items",
                {
                    "feed_source_id": source["id"],
                    "guid": guid,
                    "title": entry.get("title", "Untitled"),
                    "url": entry.get("link", ""),
                    "author": entry.get("author", ""),
                    "content": entry.get("summary", entry.get("description", "")),
                    "category": source.get("category", "general"),
                    "published_at": published.isoformat() if published else None,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "is_processed": 0,
                    "is_published": 0,
                    "relevance_score": 0.0,
                },
            )
            new_count += 1

        await self.db_update(
            "feed_sources",
            {"last_fetched": datetime.now(timezone.utc).isoformat()},
            "id = ?",
            [source["id"]],
        )

        return new_count

    async def fetch_github_releases(self) -> None:
        repos_path = self.config_dir / "github_repos.yaml"
        if not repos_path.exists():
            return

        data = yaml.safe_load(repos_path.read_text()) or {}
        for repo_def in data.get("tracked_repos", []):
            repo = repo_def["repo"]
            url = f"https://api.github.com/repos/{repo}/releases"
            try:
                response = await self.http_request(url, "GET", headers={"Accept": "application/vnd.github.v3+json"})
                releases = json.loads(response.get("body", "[]"))
            except Exception as e:
                logger.warning("Failed to fetch releases for %s: %s", repo, e)
                continue

            for rel in releases[:5]:
                guid = f"{repo}:{rel.get('tag_name')}"
                existing = await self.db_query(
                    "github_releases",
                    "SELECT id FROM github_releases WHERE guid = ?",
                    [guid],
                )
                if existing:
                    continue

                published = rel.get("published_at")
                await self.db_insert(
                    "github_releases",
                    {
                        "repo": repo,
                        "tag": rel.get("tag_name"),
                        "name": rel.get("name") or rel.get("tag_name"),
                        "body": (rel.get("body") or "")[:5000],
                        "url": rel.get("html_url", ""),
                        "published_at": published,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "is_processed": 0,
                        "is_published": 0,
                        "guid": guid,
                    },
                )

    async def process_unprocessed(self) -> None:
        items = await self.db_query(
            "feed_items",
            "SELECT id, title, category, content FROM feed_items WHERE is_processed = 0 ORDER BY fetched_at DESC LIMIT ?",
            [self.processing_batch_size],
        )
        if not items:
            return

        for item in items:
            prompt = f"""Evaluate this feed item:\n\nTitle: {item['title']}\nCategory: {item['category']}\nContent: {item['content'][:2000]}\n\n{RESEARCH_SYSTEM_PROMPT}"""
            try:
                response = await self.llm_generate(prompt, max_tokens=500)
                summary, relevance = self._parse_llm_response(response)
                await self.db_update(
                    "feed_items",
                    {
                        "summary": summary,
                        "relevance_score": relevance,
                        "is_processed": 1,
                    },
                    "id = ?",
                    [item["id"]],
                )
            except Exception as e:
                logger.warning("Failed to process item %s: %s", item.get("title"), e)

    @staticmethod
    def _parse_llm_response(text: str) -> tuple[str, float]:
        summary = ""
        relevance = 0.5
        for line in text.strip().split("\n"):
            if line.startswith("SUMMARY:"):
                summary = line[8:].strip()
            elif line.startswith("RELEVANCE:"):
                try:
                    relevance = float(line[10:].strip())
                except ValueError:
                    relevance = 0.5
        return summary, relevance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    module = ResearchModule()
    asyncio.run(module.start())
