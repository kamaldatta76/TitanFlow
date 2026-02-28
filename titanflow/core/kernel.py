"""TitanFlow v0.2 Core kernel (MVP)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass

from telegram import Bot

from titanflow.config import LLMConfig
from titanflow.core.audit import AuditLogger
from titanflow.core.auth import AuthManager
from titanflow.core.config import CoreConfig, load_core_config
from titanflow.core.database_broker import DatabaseBroker
from titanflow.core.http_proxy import HttpProxy
from titanflow.core.ipc import IPCServer, start_ipc_server
from titanflow.core.llm import LLMClient
from titanflow.core.llm_broker import LLMBroker, Priority
from titanflow.core.module_supervisor import ModuleSupervisor
from titanflow.telegram.bot import TelegramGateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("titanflow.core")


@dataclass
class TelegramConfigShim:
    bot_token: str
    allowed_users: list[int]


class DummyScheduler:
    def list_jobs(self):
        return []


class CoreEngine:
    def __init__(
        self,
        config: CoreConfig,
        llm_broker: LLMBroker,
        db: DatabaseBroker,
        auth: AuthManager,
        supervisor: ModuleSupervisor,
        audit: AuditLogger,
    ) -> None:
        self.config = type("Cfg", (), {"name": config.core.instance_name, "telegram": config.telegram})
        self.llm = llm_broker
        self.db = db
        self.auth = auth
        self.supervisor = supervisor
        self.audit_logger = audit
        self.scheduler = DummyScheduler()

    def status(self) -> dict:
        module_states = self.supervisor.status()
        manifests = self.auth.list_manifests()
        modules: dict[str, dict] = {}

        if manifests:
            for module_id, manifest in manifests.items():
                state = module_states.get(module_id, {})
                modules[module_id] = {
                    "enabled": state.get("connected", False),
                    "description": manifest.get("module", {}).get("description", ""),
                }
        else:
            for module_id, state in module_states.items():
                modules[module_id] = {
                    "enabled": state.get("connected", False),
                    "description": "",
                }

        return {
            "name": self.config.name,
            "modules": modules,
            "scheduled_jobs": self.scheduler.list_jobs(),
        }

    async def route_telegram(self, command: str, args: str, context) -> str:
        if command == "research":
            return await self._cmd_research_status()
        if command == "latest":
            return await self._cmd_latest()
        return f"Unknown command: /{command}."

    async def _cmd_research_status(self) -> str:
        feeds = await self.db.query("feed_sources", "SELECT COUNT(*) as count FROM feed_sources")
        processed = await self.db.query("feed_items", "SELECT COUNT(*) as count FROM feed_items WHERE is_processed = 1")
        pending = await self.db.query("feed_items", "SELECT COUNT(*) as count FROM feed_items WHERE is_processed = 0")
        return "\n".join([
            "📊 Research Module Status",
            f"  Feeds: {feeds[0]['count'] if feeds else 0}",
            f"  Processed items: {processed[0]['count'] if processed else 0}",
            f"  Pending: {pending[0]['count'] if pending else 0}",
        ])

    async def _cmd_latest(self) -> str:
        rows = await self.db.query(
            "feed_items",
            "SELECT title, summary, relevance_score FROM feed_items WHERE is_processed = 1 AND relevance_score >= 0.6 ORDER BY fetched_at DESC LIMIT 5",
        )
        if not rows:
            return "No high-relevance items found yet."
        lines = ["📰 Latest Research Items\n"]
        for row in rows:
            lines.append(f"• [{row['relevance_score']:.1f}] {row['title']}")
            if row.get("summary"):
                lines.append(f"  {row['summary'][:150]}")
            lines.append("")
        return "\n".join(lines)

    async def audit(self, event_type: str, command: str = "", args: str = "", result: str = "success", details: str = "", user_id=None, duration_ms: int = 0) -> None:
        await self.audit_logger.log(event_type, module_id="core", method=command, status=result, details={"args": args, "details": details}, duration_ms=duration_ms)

    async def audit_gate(self, *, user_id: int | None, gate: str, hits: int, decision: str, query: str) -> None:
        await self.audit_logger.log(
            "grounding_gate",
            module_id="core",
            method="telegram",
            status=decision,
            details={"gate": gate, "hits": hits, "decision": decision, "query": query[:200], "user_id": user_id},
        )

    async def upsert_conversation(self, chat_id: str, user_id: int | None, role: str) -> None:
        await self.db.upsert_conversation(chat_id, user_id, role)

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
        await self.db.upsert_conversation(chat_id, user_id, role)
        await self.db.insert_message(chat_id, role, text, token_est=token_est, meta_json=meta_json)

    async def load_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict[str, str]]:
        rows = await self.db.fetch_messages(chat_id, limit)
        return [{"role": row["role"], "content": row["text"]} for row in rows]

    async def load_pinned_directives(self, chat_id: str) -> list[dict[str, str]]:
        rows = await self.db.fetch_pinned_directives(chat_id)
        return [{"role": row["role"], "content": row["text"]} for row in rows]

    async def search_knowledge(self, text_query: str, limit: int = 6) -> list[dict]:
        return await self.db.search(text_query, limit=limit)

    def memory_status(self) -> str:
        if self.db:
            return "I persist chat history in TitanFlow Core and replay the recent context each message."
        return "This instance is running without persistent history; I only see what's in the current request."


async def _notify_papa(bot: Bot | None, allowed_users: list[int], message: str) -> None:
    if not bot or not allowed_users:
        return
    try:
        await bot.send_message(chat_id=allowed_users[0], text=message)
        logger.info("Sent Telegram alert to Papa")
    except Exception as exc:
        logger.warning("Failed to send Telegram alert: %s", exc)


async def main() -> None:
    config = load_core_config()

    # Build LLM client + broker
    llm_cfg = LLMConfig()
    llm_cfg.default_model = config.llm.default_model
    llm_cfg.fallback_model = config.llm.fallback_model
    llm_cfg.cloud.api_key = config.llm.cloud_api_key
    llm_cfg.cloud.model = config.llm.cloud_model
    llm_client = LLMClient(llm_cfg)
    llm_broker = LLMBroker(llm_client, semaphore_limit=config.llm.semaphore_limit)
    await llm_broker.start()

    # Core services
    db = DatabaseBroker(config.database)
    await db.init_schema()
    http_proxy = HttpProxy(config.http_proxy)

    auth = AuthManager(config.modules.manifest_dir)
    auth.load_manifests()

    bot = Bot(token=config.telegram.bot_token) if config.telegram.bot_token else None
    supervisor = ModuleSupervisor(
        notify_fn=lambda msg: _notify_papa(bot, config.telegram.allowed_users, msg),
        health_interval=config.modules.health_check_interval,
    )
    await supervisor.start()

    audit = AuditLogger(db)

    ipc_handler = IPCServer(auth, llm_broker, db, http_proxy, audit, supervisor)
    server = await start_ipc_server(config.core.socket_path, ipc_handler)

    # Telegram gateway (Core)
    engine = CoreEngine(config, llm_broker, db, auth, supervisor, audit)
    disable_gateway = os.environ.get("TITANFLOW_DISABLE_TELEGRAM_GATEWAY", "0") == "1"
    if disable_gateway:
        logger.info("Telegram gateway disabled by TITANFLOW_DISABLE_TELEGRAM_GATEWAY")
    else:
        tg_config = TelegramConfigShim(
            bot_token=config.telegram.bot_token,
            allowed_users=config.telegram.allowed_users,
        )
        telegram = TelegramGateway(engine, tg_config)
        await telegram.start()

    logger.info("TitanFlow Core running")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
