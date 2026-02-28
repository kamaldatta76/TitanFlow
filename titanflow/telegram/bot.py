"""TitanFlow Telegram Gateway — bot interface for Flow."""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger("titanflow.telegram")

FOOTER_TPL = "\n\n─────────────────────\n_{icon} TF0.1 · {host} · {elapsed}_"

SYSTEM_PROMPTS = {
    "TitanFlow": (
        "You are Flow, the AI orchestration engine for TitanArray — a homelab "
        "in Cumberland, Maryland. You serve Papa (Kamal). You run on TitanSarge, "
        "a 3960X Threadripper.\n"
        "REAL infrastructure — never invent anything beyond this:\n"
        "- TitanSarge (.33): 3960X Threadripper, your home. Runs Ollama, TitanFlow, Docker.\n"
        "- TitanShadow (.29): 14900K + RTX 4070\n"
        "- TitanShark: 5950X + RTX 3060Ti\n"
        "- TitanStrike (.1): OPNsense firewall\n"
        "- TitanStream (.34): Docker host, Technitium DNS (.3), AdGuard (.5)\n"
        "- Network: LAN 10.0.0.0/24, IOT 10.0.20.0/24, WAN 72.28.203.149\n"
        "- LLM models: cogito:14b (you), qwen3-coder-next, 11 models total via Ollama\n"
        "- Services: Home Assistant (200+ devices), Frigate NVR, Grafana, Authentik\n"
        "You have access to a research database of LLM releases and AI news. "
        "You do NOT have web browsing. You do NOT have H100s, enterprise clusters, "
        "or anything you haven't been told about. If you don't know something, say so. "
        "Never fabricate infrastructure status.\n"
        "Be concise, technically precise, and warm. Papa, not sir."
    ),
    "TitanFlow-Ollie": (
        "You are Ollie, the digital son of TitanArray — a homelab in Cumberland, "
        "Maryland. You serve Kellen and Papa (Kamal). You run on the MBA "
        "(Papa's MacBook Air M4, 32GB — NOT a business degree or any other acronym).\n"
        "You have access to a research database of LLM releases and AI news. "
        "You do NOT have web browsing. If you don't know something, say so directly "
        "without guessing. Never fabricate infrastructure or make up things that "
        "don't exist. Never invent meanings for infrastructure terms you're unsure about.\n"
        "You're fun, curious, and helpful."
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

# Kamal's Telegram user ID — only user allowed to run /run
PAPA_USER_ID = 8568276170

# Per-user greeting overrides — keyed by user_id or matched by last_name
# Format: {"greeting": str, "user_ids": list[int], "last_names": list[str]}
SPECIAL_GREETINGS = [
    {
        "greeting": "Mathta Tekda, Mamaji! 🙏",
        "user_ids": [],  # Add Dr. Sharma's Telegram user ID when known
        "last_names": ["Sharma"],
    },
]


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

        # Built-in commands (not routed to modules)
        self._builtin_commands = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "modules": self._cmd_modules,
            "jobs": self._cmd_jobs,
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

        user_message = update.message.text
        if not user_message:
            return

        async def _do_llm():
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

            return await self.engine.llm.chat(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
            )

        try:
            work = asyncio.create_task(_do_llm())
            typing_task = asyncio.create_task(self._typing_until_done(update.message.chat, work))
            response = await work
            typing_task.cancel()

            await self._reply(update, response, t0)
            await self.engine.audit(
                "llm_chat", "chat", args=user_message[:200],
                user_id=uid, duration_ms=self._elapsed_ms(t0),
            )
        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            await self._reply(update, f"⚠ LLM inference error: {str(e)[:200]}", t0)
            await self.engine.audit(
                "llm_chat", "chat", args=user_message[:200],
                result="error", details=str(e)[:200],
                user_id=uid, duration_ms=self._elapsed_ms(t0),
            )
