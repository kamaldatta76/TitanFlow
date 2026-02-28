"""TitanFlow Engine — the central orchestration core."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from titanflow.config import TitanFlowConfig
from titanflow.core.database import Database
from titanflow.core.events import EventBus
from titanflow.core.llm import LLMClient
from titanflow.core.scheduler import Scheduler
from titanflow.models import AuditLog, Conversation, Message, PinnedDirective
from titanflow.modules.base import BaseModule

logger = logging.getLogger("titanflow.engine")


class TitanFlowEngine:
    """Central orchestration engine.

    Owns all shared services and manages module lifecycle.
    """

    def __init__(self, config: TitanFlowConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.llm = LLMClient(config.llm)
        self.scheduler = Scheduler()
        self.db = Database(config.database)
        self._modules: dict[str, BaseModule] = {}

    def register_module(self, module: BaseModule) -> None:
        """Register a module with the engine."""
        self._modules[module.name] = module
        logger.info(f"Registered module: {module.name}")

    async def start(self) -> None:
        """Initialize all services and start all enabled modules."""
        logger.info("═" * 50)
        logger.info("  TitanFlow Engine starting...")
        logger.info("═" * 50)

        # Initialize database
        await self.db.init()
        logger.info("✓ Database initialized")

        # Start scheduler
        self.scheduler.start()
        logger.info("✓ Scheduler started")

        # Check LLM health
        health = await self.llm.health_check()
        if health["status"] == "ok":
            logger.info(f"✓ LLM connected — {len(health['models'])} model(s) available")
        else:
            logger.warning(f"⚠ LLM health check failed: {health.get('error', 'unknown')}")

        # Start enabled modules
        for name, module in self._modules.items():
            if module.enabled:
                try:
                    await module.start()
                    logger.info(f"✓ Module started: {name}")
                except Exception:
                    logger.exception(f"✗ Failed to start module: {name}")
            else:
                logger.info(f"○ Module disabled: {name}")

        await self.events.emit("engine.started", source="engine")
        logger.info("═" * 50)
        logger.info(f"  TitanFlow ready — {len(self.active_modules)} module(s) active")
        logger.info("═" * 50)

    async def shutdown(self) -> None:
        """Stop all modules and services."""
        logger.info("TitanFlow shutting down...")

        # Stop modules in reverse order
        for name in reversed(list(self._modules.keys())):
            module = self._modules[name]
            if module.enabled:
                try:
                    await module.stop()
                    logger.info(f"Stopped module: {name}")
                except Exception:
                    logger.exception(f"Error stopping module: {name}")

        # Shutdown services
        self.scheduler.shutdown()
        await self.llm.close()
        await self.db.close()

        await self.events.emit("engine.stopped", source="engine")
        logger.info("TitanFlow shutdown complete")

    @property
    def active_modules(self) -> list[str]:
        return [name for name, m in self._modules.items() if m.enabled]

    @property
    def modules(self) -> dict[str, BaseModule]:
        return dict(self._modules)

    def get_module(self, name: str) -> BaseModule | None:
        return self._modules.get(name)

    async def route_telegram(self, command: str, args: str, context: Any) -> str:
        """Route a Telegram command to the appropriate module.

        Tries each module until one handles it.
        """
        for module in self._modules.values():
            if not module.enabled:
                continue
            result = await module.handle_telegram(command, args, context)
            if result is not None:
                return result

        return f"Unknown command: /{command}. Use /help to see available commands."

    async def audit(
        self,
        event_type: str,
        command: str = "",
        args: str = "",
        result: str = "success",
        details: str = "",
        user_id: int | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Write an entry to the audit log. Fire-and-forget."""
        try:
            async with self.db.session() as session:
                entry = AuditLog(
                    event_type=event_type,
                    user_id=user_id,
                    command=command,
                    args=args[:500],
                    result=result,
                    details=details[:1000],
                    duration_ms=duration_ms,
                )
                session.add(entry)
                await session.commit()
        except Exception:
            logger.debug("Audit log write failed", exc_info=True)

    async def audit_gate(
        self,
        *,
        user_id: int | None,
        gate: str,
        hits: int,
        decision: str,
        query: str,
    ) -> None:
        details = f"gate={gate} hits={hits} decision={decision} query={query[:200]}"
        await self.audit(
            "grounding_gate",
            "telegram",
            args=query[:200],
            result=decision,
            details=details,
            user_id=user_id,
        )

    async def upsert_conversation(self, chat_id: str, user_id: int | None, role: str) -> None:
        async with self.db.session() as session:
            convo = await session.get(Conversation, chat_id)
            now = datetime.now(timezone.utc)
            if convo is None:
                convo = Conversation(
                    chat_id=chat_id,
                    user_id=user_id,
                    role=role,
                    created_at=now,
                    last_seen_at=now,
                )
                session.add(convo)
            else:
                convo.user_id = user_id
                convo.role = role
                convo.last_seen_at = now
            await session.commit()

    async def persist_message(
        self,
        *,
        chat_id: str,
        user_id: int | None,
        role: str,
        text: str,
        token_est: int = 0,
        meta_json: str = "{}",
    ) -> None:
        await self.upsert_conversation(chat_id, user_id, role)
        async with self.db.session() as session:
            msg = Message(
                chat_id=chat_id,
                role=role,
                text=text,
                ts=datetime.now(timezone.utc),
                token_est=token_est,
                meta_json=meta_json,
            )
            session.add(msg)
            await session.commit()

    async def load_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict[str, str]]:
        async with self.db.session() as session:
            stmt = select(Message).where(Message.chat_id == chat_id).order_by(Message.ts.desc()).limit(limit)
            result = await session.exec(stmt)
            rows = list(result)
        rows.reverse()
        return [{"role": row.role, "content": row.text} for row in rows]

    async def load_pinned_directives(self, chat_id: str) -> list[dict[str, str]]:
        async with self.db.session() as session:
            stmt = select(PinnedDirective).where(
                (PinnedDirective.is_active == True)  # noqa: E712
                & ((PinnedDirective.scope == "global") | (PinnedDirective.chat_id == chat_id))
            )
            result = await session.exec(stmt)
            rows = list(result)
        return [{"role": row.role, "content": row.text} for row in rows]

    async def search_knowledge(self, text_query: str, limit: int = 6) -> list[dict[str, Any]]:
        terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", text_query)]
        if not terms:
            return []

        def _build_like_clause(columns: list[str]) -> tuple[str, list[str]]:
            clauses: list[str] = []
            params: list[str] = []
            for term in terms:
                pattern = f"%{term}%"
                col_clause = " OR ".join([f"{col} LIKE ?" for col in columns])
                clauses.append(f"({col_clause})")
                params.extend([pattern] * len(columns))
            return " OR ".join(clauses), params

        feed_where, feed_params = _build_like_clause(["title", "summary", "content"])
        summary_where, summary_params = _build_like_clause(["summary"])
        article_where, article_params = _build_like_clause(
            ["title", "excerpt", "content_markdown", "content_html"]
        )

        sql = f"""
        SELECT source_table, source_id, title, snippet, url, sort_ts FROM (
            SELECT 'feed_items' AS source_table,
                   id AS source_id,
                   title AS title,
                   COALESCE(NULLIF(summary, ''), substr(content, 1, 300)) AS snippet,
                   url AS url,
                   fetched_at AS sort_ts
            FROM feed_items
            WHERE {feed_where}
            UNION ALL
            SELECT 'research_summaries' AS source_table,
                   rs.id AS source_id,
                   COALESCE(fi.title, 'Research Summary') AS title,
                   rs.summary AS snippet,
                   COALESCE(fi.url, '') AS url,
                   rs.created_at AS sort_ts
            FROM research_summaries rs
            LEFT JOIN feed_items fi ON fi.id = rs.feed_item_id
            WHERE {summary_where}
            UNION ALL
            SELECT 'articles' AS source_table,
                   id AS source_id,
                   title AS title,
                   COALESCE(NULLIF(excerpt, ''), substr(content_markdown, 1, 300)) AS snippet,
                   '' AS url,
                   created_at AS sort_ts
            FROM articles
            WHERE {article_where}
        )
        ORDER BY sort_ts DESC
        LIMIT ?
        """
        params = tuple(feed_params + summary_params + article_params + [limit])

        async with self.db.session() as session:
            result = await session.exec(text(sql), params)
            rows = result.all()
        return [
            {
                "source_table": row[0],
                "source_id": row[1],
                "title": row[2],
                "snippet": row[3],
                "url": row[4],
            }
            for row in rows
        ]

    def memory_status(self) -> str:
        if self.db:
            return "I persist chat history in TitanFlow Core and replay the recent context each message."
        return (
            "This instance is running without persistent history; I only see what's in the current request."
        )

    def status(self) -> dict[str, Any]:
        """Get engine status overview."""
        return {
            "name": self.config.name,
            "modules": {
                name: {
                    "enabled": m.enabled,
                    "description": m.description,
                }
                for name, m in self._modules.items()
            },
            "scheduled_jobs": self.scheduler.list_jobs(),
        }
