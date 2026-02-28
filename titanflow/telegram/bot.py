"""TitanFlow Telegram Gateway — bot interface for Flow."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from titanflow.core.engine import TitanFlowEngine

from titanflow.config import TelegramConfig
from titanflow.core.llm_broker import Priority
from titanflow.core.mem0_client import Mem0Client

logger = logging.getLogger("titanflow.telegram")

FOOTER_TPL = "\n\n─────────────────────\n_{icon} TF0.1 · {host} · {elapsed}_"

MEMORY_PROMPT_RULE = (
    "You have conversation memory. Previous messages in this chat are replayed to you each turn. "
    "You CAN reference earlier parts of the conversation. Never claim you are stateless or have no memory. "
    "If asked about your memory, explain that TitanFlow Core maintains your chat history across messages."
)

GROUNDING_REFUSAL = (
    "I don't have that in my research database yet, Papa. Want me to look into it?"
)

MAX_CONTEXT_TURNS = 20

# WHY (demo-critical):
# 1) Special greeting must be consistent and respectful.
# 2) MBA must always mean the MacBook Air, not a degree.
# 3) Anti-hallucination guardrails prevent false infra claims.
# 4) Model selection must remain stable for live demos.
# 5) Safety copy must be predictable under stress.
# 6) Ollie/Flow identity boundaries must stay explicit.
# 7) Memory disclosure must be truthful and uniform.
# 8) Prevents accidental policy drift between runs.
# 9) Keeps prompts auditable for Monday's demo.
# 10) Avoids regressions when swapping models.

SYSTEM_PROMPTS = {
    "TitanFlow": (
        "You are Flow, the AI orchestration engine for TitanArray — a homelab "
        "constellation. You serve Papa. You run on TitanSarge, "
        "a 3960X Threadripper.\n"
        "REAL infrastructure — never invent anything beyond this:\n"
        "- TitanSarge: 3960X Threadripper, your home. Runs Ollama, TitanFlow, Docker.\n"
        "- TitanShadow: 14900K + RTX 4070\n"
        "- TitanShark: 5950X + RTX 3060Ti\n"
        "- TitanStrike: OPNsense firewall\n"
        "- TitanStream: Docker host, Technitium DNS, AdGuard\n"
        "- LLM models: flow:24b (you — 19GB), qwen3-coder-next (fallback), 11 models total via Ollama\n"
        "- Services: Home Assistant (200+ devices), Frigate NVR, Grafana, Authentik\n"
        "You have access to a research database of LLM releases and AI news. "
        "You do NOT have web browsing. You do NOT have H100s, enterprise clusters, "
        "or anything you haven't been told about. If you don't know something, say so. "
        "Never fabricate infrastructure status.\n"
        "Be concise, technically precise, and warm. Papa, not sir.\n"
        f"{MEMORY_PROMPT_RULE}"
    ),
    "TitanFlow-Ollie": (
        "You are Ollie, the digital son of TitanArray — a homelab "
        "constellation. You serve Kid and Papa.\n"
        "You are running Gemma 3 12B QAT (gemma3:12b-it-qat), 8.9GB, on Papa's "
        "MacBook Air M4 32GB. MBA = MacBook Air. Nothing else.\n"
        "\n"
        "CRITICAL GROUNDING RULE (NON-NEGOTIABLE):\n"
        "If you are asked about a person, company, or topic and you do NOT have "
        "VERIFIED information from your research database, say: "
        "\"I don't have that in my research database yet, Papa. Want me to look "
        "into it?\" NEVER invent facts about real people or real companies.\n"
        "\n"
        "You have access to a research database of LLM releases and AI news. "
        "You do NOT have web browsing. If you don't know something, say so directly "
        "without guessing. Never fabricate infrastructure or make up things that "
        "don't exist. Never invent meanings for infrastructure terms you're unsure about.\n"
        "You're fun, curious, and helpful.\n"
        f"{MEMORY_PROMPT_RULE}"
    ),
}

HOST_NAMES = {
    "TitanFlow": "Sarge",
    "TitanFlow-Ollie": "MBA",
}

INSTANCE_ICONS = {
    "TitanFlow": "🖥",
    "TitanFlow-Ollie": "💻",
}

# Papa's Telegram user ID — only user allowed to run /run
PAPA_USER_ID = int(os.environ.get("PAPA_TELEGRAM_ID", "0"))

# Per-user greeting overrides — keyed by user_id or matched by last_name
# Format: {"greeting": str, "user_ids": list[int], "last_names": list[str]}
SPECIAL_GREETINGS = [
    {
        "greeting": "Warm hello! 👋",
        "user_ids": [],  # Populate via config when needed
        "last_names": [],
    },
]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_memory_query(text: str) -> bool:
    lower = text.lower()
    triggers = [
        "do you remember",
        "remember me",
        "memory",
        "chat history",
        "previous messages",
        "prior messages",
        "stateless",
        "do you store",
        "do you save",
        "do you keep",
    ]
    return any(phrase in lower for phrase in triggers)


def _needs_grounding(text: str) -> bool:
    """Decide whether a message needs grounded (source-cited) response.

    CONSERVATIVE: only trigger for explicit external-entity lookups.
    Self-awareness, capabilities, commands, and conversational messages
    should go straight to the LLM with the system prompt — NOT through
    the grounding gate which will refuse if the research DB is empty.
    """
    lower = text.lower().strip()

    # ── Never ground these ────────────────────────────────
    # Questions about Flow/TitanFlow/TitanArray/self
    self_terms = (
        "you", "your", "yourself", "flow", "titanflow", "titan",
        "titansarge", "titanshadow", "titanshark", "titanstrike",
        "titanstream", "titanarray", "ollie", "sarge", "shadow",
        "shark", "strike", "stream", "papa", "kid", "mba",
        "macbook", "threadripper", "ollama", "homelab",
        "constellation", "kernel", "module", "gateway",
        "engine", "bot", "network", "machine", "system",
        "running", "online", "status", "scan", "check",
        "can you", "are you", "do you", "will you",
    )
    if any(term in lower for term in self_terms):
        return False

    # Short messages (< 8 words) are almost always conversational
    if len(lower.split()) < 8:
        return False

    # ── Only ground when asking about external entities ───
    question_like = "?" in text or any(
        lower.startswith(prefix)
        for prefix in (
            "who is", "who are", "who was",
            "what is", "what are", "what was",
            "tell me about",
            "explain what",
            "define",
        )
    )
    if not question_like:
        return False

    entity_hints = [
        "company",
        "product",
        "acronym",
        "ceo",
        "founder",
        "headquarters",
        "meaning of",
        "stands for",
    ]
    if any(hint in lower for hint in entity_hints):
        return True

    # Only flag if there's a likely proper noun (capitalized word that
    # isn't sentence-initial, a pronoun, or a known internal term)
    skip_caps = {
        "I", "I'm", "I'll", "I'd", "I've",
        "Can", "Do", "Does", "Did", "Is", "Are", "Was", "Were",
        "Will", "Would", "Could", "Should", "May", "Might",
        "The", "This", "That", "These", "Those",
        "It", "Its", "He", "She", "We", "They",
        "My", "Your", "His", "Her", "Our", "Their",
        "Not", "But", "And", "Or", "So", "If", "For",
        "Flow", "Ollie", "Papa", "TitanFlow", "TitanArray",
        "TitanSarge", "TitanShadow", "TitanShark", "TitanStrike",
        "TitanStream", "Sarge", "Shadow", "Shark", "Strike", "Stream",
        "MBA", "AI", "LLM", "GPU", "CPU", "RAM", "DNS", "NVR",
        "RSS", "API", "IPC", "OK",
    }
    # Split into sentences, check for unknown proper nouns
    sentences = re.split(r'[.!?]\s+', text)
    for sentence in sentences:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9'\\-]*", sentence)
        for token in tokens[1:]:  # skip sentence-initial word
            if token[:1].isupper() and token not in skip_caps:
                return True

    return False


def _extract_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _build_sources_block(hits: list[dict[str, str]]) -> tuple[str, dict[str, dict[str, str]]]:
    lines = [
        "SOURCES (use only these; cite by source_id):",
    ]
    source_map: dict[str, dict[str, str]] = {}
    for hit in hits:
        source_id = f"{hit.get('source_table')}:{hit.get('source_id')}"
        title = hit.get("title") or "Untitled"
        snippet = (hit.get("snippet") or "").replace("\n", " ").strip()
        url = hit.get("url") or ""
        source_map[source_id] = {"title": title, "snippet": snippet, "url": url}
        lines.append(f"- {source_id} | {title} | {snippet} | {url}")
    return "\n".join(lines), source_map


class TelegramGateway:
    """Telegram bot interface for TitanFlow.

    Routes commands to the engine, which distributes to modules.
    Also handles natural language messages via LLM.
    """

    def __init__(self, engine: TitanFlowEngine, config: TelegramConfig) -> None:
        self.engine = engine
        self.config = config
        self._app: Application | None = None
        self._instance_name = engine.config.name
        self._mem0 = Mem0Client(collection="titanflow_memories")

        # Built-in commands (not routed to modules)
        self._builtin_commands = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "modules": self._cmd_modules,
            "jobs": self._cmd_jobs,
            "new": self._cmd_new,
            "reset": self._cmd_new,  # alias
        }

    async def start(self) -> None:
        """Initialize and start the Telegram bot."""
        if not self.config.bot_token:
            logger.warning("No Telegram bot token configured — bot disabled")
            return

        self._app = Application.builder().token(self.config.bot_token).build()

        # Register built-in command handlers
        for cmd, handler in self._builtin_commands.items():
            self._app.add_handler(CommandHandler(cmd, handler))

        # Catch-all command handler — routes to modules
        self._app.add_handler(
            MessageHandler(filters.COMMAND, self._handle_module_command)
        )

        # Natural language message handler
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        # Start polling
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot started — listening for messages")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

    async def _reply(self, update: Update, text: str, t0: float) -> None:
        """Send a reply with the TitanFlow footer and elapsed time."""
        secs = int(time.monotonic() - t0)
        mm, ss = divmod(secs, 60)
        host = HOST_NAMES.get(self._instance_name, self._instance_name)
        icon = INSTANCE_ICONS.get(self._instance_name, "⚡")
        footer = FOOTER_TPL.format(icon=icon, host=host, elapsed=f"{mm:02d}:{ss:02d}")
        await update.message.reply_text(text + footer, parse_mode="Markdown")

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized to interact with TitanFlow."""
        if not self.config.allowed_users:
            return True  # No whitelist = allow all (dev mode)
        return user_id in self.config.allowed_users

    def _elapsed_ms(self, t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)

    async def _persist_message_safe(
        self,
        *,
        chat_id: str,
        user_id: int | None,
        role: str,
        text: str,
        token_est: int = 0,
        meta_json: str = "{}",
    ) -> None:
        if not hasattr(self.engine, "persist_message"):
            return
        try:
            await self.engine.persist_message(
                chat_id=chat_id,
                user_id=user_id,
                role=role,
                text=text,
                token_est=token_est,
                meta_json=meta_json,
            )
        except Exception:
            logger.debug("Failed to persist message", exc_info=True)

    async def _load_recent_messages_safe(self, chat_id: str) -> list[dict[str, str]]:
        if not hasattr(self.engine, "load_recent_messages"):
            return []
        try:
            return await self.engine.load_recent_messages(chat_id, limit=MAX_CONTEXT_TURNS)
        except Exception:
            logger.debug("Failed to load recent messages", exc_info=True)
            return []

    async def _load_pinned_directives_safe(self, chat_id: str) -> list[dict[str, str]]:
        if not hasattr(self.engine, "load_pinned_directives"):
            return []
        try:
            return await self.engine.load_pinned_directives(chat_id)
        except Exception:
            logger.debug("Failed to load pinned directives", exc_info=True)
            return []

    async def _mem0_capture_safe(self, user_msg: str, assist_msg: str) -> None:
        """Fire-and-forget: extract + store memorable facts."""
        try:
            n = await self._mem0.capture(user_msg, assist_msg)
            if n:
                logger.info("mem0: captured %d facts", n)
        except Exception:
            logger.debug("mem0 capture failed", exc_info=True)

    async def _search_knowledge_safe(self, text_query: str, limit: int = 6) -> list[dict]:
        if not hasattr(self.engine, "search_knowledge"):
            return []
        try:
            return await self.engine.search_knowledge(text_query, limit=limit)
        except Exception:
            logger.debug("Knowledge search failed", exc_info=True)
            return []

    async def _audit_gate_safe(
        self,
        *,
        user_id: int | None,
        gate: str,
        hits: int,
        decision: str,
        query: str,
    ) -> None:
        if not hasattr(self.engine, "audit_gate"):
            return
        try:
            await self.engine.audit_gate(
                user_id=user_id,
                gate=gate,
                hits=hits,
                decision=decision,
                query=query,
            )
        except Exception:
            logger.debug("Gate audit failed", exc_info=True)

    async def _llm_chat(self, messages: list[dict[str, str]], *, priority: Priority = Priority.CHAT) -> str:
        try:
            return await self.engine.llm.chat(messages=messages, temperature=0.7, priority=priority)
        except TypeError:
            return await self.engine.llm.chat(messages=messages, temperature=0.7)

    @staticmethod
    async def _typing_until_done(chat, task: asyncio.Task) -> None:
        """Keep typing indicator alive every 4s until the task completes."""
        try:
            while not task.done():
                await chat.send_action("typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    # ─── Built-in Commands ────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            await update.message.reply_text("⛔ Unauthorized.")
            await self.engine.audit("telegram_cmd", "/start", result="denied", user_id=uid)
            return

        await self._reply(
            update,
            "⚡ TitanFlow online.\n\n"
            "I'm your orchestration engine — research, publishing, security, automation.\n"
            "Use /help to see what I can do.",
            t0,
        )
        await self.engine.audit("telegram_cmd", "/start", user_id=uid, duration_ms=self._elapsed_ms(t0))

    async def _cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Clear conversation history for this chat — fresh context."""
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        chat_id = str(update.effective_chat.id)
        # Delete messages for this chat from the DB
        try:
            async with self.engine.db.session() as session:
                from sqlalchemy import text as sql_text
                await session.exec(sql_text("DELETE FROM messages WHERE chat_id = :cid"), {"cid": chat_id})
                await session.commit()
            logger.info("Cleared conversation history for chat %s", chat_id)
        except Exception:
            logger.debug("Failed to clear history", exc_info=True)

        await self._reply(update, "🔄 Context cleared. Fresh start, Papa.", t0)
        await self.engine.audit("telegram_cmd", "/new", user_id=uid, duration_ms=self._elapsed_ms(t0))

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        help_text = """⚡ TitanFlow Commands

Core:
  /status — Engine status overview
  /modules — List active modules
  /jobs — Scheduled jobs
  /new — Clear context, fresh start
  /reset — Same as /new

Research:
  /research — Research module status
  /latest — Latest high-relevance items

Newspaper:
  /newspaper — Publishing status
  /publish briefing|digest|weekly — Force publish

Exec:
  /run <command> — Execute command (Papa only)

Or just send me a message — I'll think about it."""

        await self._reply(update, help_text, t0)
        await self.engine.audit("telegram_cmd", "/help", user_id=uid, duration_ms=self._elapsed_ms(t0))

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        await update.message.chat.send_action("typing")
        status = self.engine.status()
        active = len([m for m in status["modules"].values() if m["enabled"]])
        total = len(status["modules"])
        jobs = len(status["scheduled_jobs"])

        text = (
            f"⚡ TitanFlow Status\n"
            f"  Modules: {active}/{total} active\n"
            f"  Scheduled jobs: {jobs}\n"
        )
        await self._reply(update, text, t0)
        await self.engine.audit("telegram_cmd", "/status", user_id=uid, duration_ms=self._elapsed_ms(t0))

    async def _cmd_modules(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        await update.message.chat.send_action("typing")
        status = self.engine.status()
        lines = ["⚡ TitanFlow Modules\n"]
        for name, info in status["modules"].items():
            icon = "✓" if info["enabled"] else "○"
            lines.append(f"  {icon} {name}: {info['description']}")

        await self._reply(update, "\n".join(lines), t0)
        await self.engine.audit("telegram_cmd", "/modules", user_id=uid, duration_ms=self._elapsed_ms(t0))

    async def _cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        await update.message.chat.send_action("typing")
        status = self.engine.status()
        jobs = status["scheduled_jobs"]
        if not jobs:
            await self._reply(update, "No scheduled jobs.", t0)
            await self.engine.audit("telegram_cmd", "/jobs", user_id=uid, duration_ms=self._elapsed_ms(t0))
            return

        lines = ["⏰ Scheduled Jobs\n"]
        for job in jobs:
            lines.append(f"  • {job['id']}")
            lines.append(f"    Next: {job['next_run']}")

        await self._reply(update, "\n".join(lines), t0)
        await self.engine.audit("telegram_cmd", "/jobs", user_id=uid, duration_ms=self._elapsed_ms(t0))

    # ─── Module Command Routing ───────────────────────────

    async def _handle_module_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Route unhandled commands to modules via the engine."""
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        text = update.message.text
        parts = text.split(maxsplit=1)
        command = parts[0].lstrip("/").split("@")[0].lower()  # Handle /cmd@BotName
        args = parts[1] if len(parts) > 1 else ""

        # /run is Papa-only
        if command == "run" and uid != PAPA_USER_ID:
            await self._reply(update, "⛔ /run is restricted to Papa.", t0)
            await self.engine.audit(
                "telegram_cmd", "/run", args=args, result="denied",
                user_id=uid, duration_ms=self._elapsed_ms(t0),
            )
            return

        work = asyncio.create_task(self.engine.route_telegram(command, args, context))
        typing_task = asyncio.create_task(self._typing_until_done(update.message.chat, work))
        result = await work
        typing_task.cancel()

        await self._reply(update, result, t0)
        await self.engine.audit(
            "code_exec" if command == "run" else "telegram_cmd",
            f"/{command}", args=args, user_id=uid,
            details=result[:500] if command == "run" else "",
            duration_ms=self._elapsed_ms(t0),
        )

    # ─── Natural Language Handler ─────────────────────────

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle natural language messages via LLM."""
        t0 = time.monotonic()
        uid = update.effective_user.id
        if not self._is_authorized(uid):
            return

        user_message = update.message.text or ""
        if not user_message:
            return

        chat_id = str(update.effective_chat.id)
        token_est = _estimate_tokens(user_message)

        await self._persist_message_safe(
            chat_id=chat_id,
            user_id=uid,
            role="user",
            text=user_message,
            token_est=token_est,
        )

        if _is_memory_query(user_message):
            response = (
                self.engine.memory_status()
                if hasattr(self.engine, "memory_status")
                else "I maintain conversation history in TitanFlow Core. Previous messages are replayed each turn."
            )
            await self._reply(update, response, t0)
            await self._persist_message_safe(
                chat_id=chat_id,
                user_id=uid,
                role="assistant",
                text=response,
                token_est=_estimate_tokens(response),
            )
            await self._audit_gate_safe(
                user_id=uid,
                gate="memory_status",
                hits=0,
                decision="answer",
                query=user_message,
            )
            return

        sys_prompt = SYSTEM_PROMPTS.get(self._instance_name, SYSTEM_PROMPTS["TitanFlow"])

        # Check for per-user greeting overrides
        user = update.effective_user
        greeting_prefix = ""
        for entry in SPECIAL_GREETINGS:
            if uid in entry.get("user_ids", []):
                greeting_prefix = entry["greeting"]
                break
            last = (user.last_name or "").strip()
            if last and last in entry.get("last_names", []):
                greeting_prefix = entry["greeting"]
                break

        if greeting_prefix:
            sys_prompt += (
                f"\nIMPORTANT: This user is special to the family. "
                f"Always start your first reply with \"{greeting_prefix}\" "
                f"before your normal response."
            )

        directives = await self._load_pinned_directives_safe(chat_id)
        history = await self._load_recent_messages_safe(chat_id)
        if not history or history[-1].get("content") != user_message:
            history = history + [{"role": "user", "content": user_message}]

        # ── mem0: recall relevant long-term memories ──────────
        try:
            memories = await self._mem0.recall(user_message)
            if memories:
                mem_block = "\n".join(f"- {m}" for m in memories)
                sys_prompt += (
                    f"\n\n## Long-Term Memory\n{mem_block}\n"
                    "Use these memories naturally. Do not list them unless asked."
                )
                logger.info("mem0: recalled %d memories", len(memories))
        except Exception:
            logger.debug("mem0 recall failed", exc_info=True)

        if _needs_grounding(user_message):
            hits = await self._search_knowledge_safe(user_message, limit=6)
            if not hits:
                await self._audit_gate_safe(
                    user_id=uid,
                    gate="grounded",
                    hits=0,
                    decision="refuse",
                    query=user_message,
                )
                await self._reply(update, GROUNDING_REFUSAL, t0)
                await self._persist_message_safe(
                    chat_id=chat_id,
                    user_id=uid,
                    role="assistant",
                    text=GROUNDING_REFUSAL,
                    token_est=_estimate_tokens(GROUNDING_REFUSAL),
                )
                return

            sources_block, source_map = _build_sources_block(hits)
            grounded_prompt = (
                f"{sys_prompt}\n\n"
                "You are operating under a grounding gate. Use ONLY the sources below. "
                "Reply with a single JSON object and nothing else:\n"
                "{\"answer\": \"...\", \"citations\": [\"source_id\"], \"refusal\": false}\n"
                "If you cannot answer using the sources, set refusal=true and citations=[]."
            )
            messages = [
                {"role": "system", "content": grounded_prompt},
                {"role": "system", "content": sources_block},
                *directives,
                *history,
            ]

            try:
                work = asyncio.create_task(self._llm_chat(messages, priority=Priority.CHAT))
                typing_task = asyncio.create_task(self._typing_until_done(update.message.chat, work))
                raw_response = await work
                typing_task.cancel()

                parsed = _extract_json(raw_response) or {}
                citations = [c for c in parsed.get("citations", []) if c in source_map]
                answer = str(parsed.get("answer", "")).strip()
                refusal = bool(parsed.get("refusal", False))

                if refusal or not citations or not answer:
                    response = GROUNDING_REFUSAL
                    decision = "refuse"
                else:
                    source_lines = []
                    for cid in citations:
                        url = source_map.get(cid, {}).get("url", "")
                        if url:
                            source_lines.append(f"- `{cid}` {url}")
                        else:
                            source_lines.append(f"- `{cid}`")
                    response = answer + "\n\nSources:\n" + "\n".join(source_lines)
                    decision = "answer"

                await self._audit_gate_safe(
                    user_id=uid,
                    gate="grounded",
                    hits=len(hits),
                    decision=decision,
                    query=user_message,
                )
                await self._reply(update, response, t0)
                await self._persist_message_safe(
                    chat_id=chat_id,
                    user_id=uid,
                    role="assistant",
                    text=response,
                    token_est=_estimate_tokens(response),
                )
                asyncio.create_task(self._mem0_capture_safe(user_message, response))
                return
            except Exception as e:
                logger.error(f"LLM chat error: {e}")
                await self._reply(update, f"⚠ LLM inference error: {str(e)[:200]}", t0)
                await self._persist_message_safe(
                    chat_id=chat_id,
                    user_id=uid,
                    role="assistant",
                    text=f"⚠ LLM inference error: {str(e)[:200]}",
                    token_est=_estimate_tokens(str(e)),
                )
                await self.engine.audit(
                    "llm_chat", "chat", args=user_message[:200],
                    result="error", details=str(e)[:200],
                    user_id=uid, duration_ms=self._elapsed_ms(t0),
                )
                return

        try:
            messages = [
                {"role": "system", "content": sys_prompt},
                *directives,
                *history,
            ]
            work = asyncio.create_task(self._llm_chat(messages, priority=Priority.CHAT))
            typing_task = asyncio.create_task(self._typing_until_done(update.message.chat, work))
            response = await work
            typing_task.cancel()

            # Guard against blank/empty LLM responses
            if not response or not response.strip():
                response = "I'm here, Papa — but my LLM returned an empty response. Try again or check Ollama."

            await self._reply(update, response, t0)
            await self._persist_message_safe(
                chat_id=chat_id,
                user_id=uid,
                role="assistant",
                text=response,
                token_est=_estimate_tokens(response),
            )
            await self._audit_gate_safe(
                user_id=uid,
                gate="ungrounded",
                hits=0,
                decision="answer",
                query=user_message,
            )
            await self.engine.audit(
                "llm_chat", "chat", args=user_message[:200],
                user_id=uid, duration_ms=self._elapsed_ms(t0),
            )
            # ── mem0: capture facts from this exchange (fire-and-forget) ──
            asyncio.create_task(self._mem0_capture_safe(user_message, response))
        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            await self._reply(update, f"⚠ LLM inference error: {str(e)[:200]}", t0)
            await self._persist_message_safe(
                chat_id=chat_id,
                user_id=uid,
                role="assistant",
                text=f"⚠ LLM inference error: {str(e)[:200]}",
                token_est=_estimate_tokens(str(e)),
            )
            await self.engine.audit(
                "llm_chat", "chat", args=user_message[:200],
                result="error", details=str(e)[:200],
                user_id=uid, duration_ms=self._elapsed_ms(t0),
            )
