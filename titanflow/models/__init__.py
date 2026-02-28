"""TitanFlow Database Models — research feeds, articles, and logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


# ─── Research Models ───────────────────────────────────────

class FeedSource(SQLModel, table=True):
    """An RSS/Atom feed source being tracked."""

    __tablename__ = "feed_sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True, index=True)
    name: str = ""
    category: str = "general"
    last_fetched: Optional[datetime] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FeedItem(SQLModel, table=True):
    """An individual item from a feed."""

    __tablename__ = "feed_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    feed_source_id: int = Field(index=True)
    guid: str = Field(unique=True, index=True)  # dedup key
    title: str
    url: str = ""
    author: str = ""
    content: str = ""
    summary: str = ""  # LLM-generated summary
    category: str = "general"
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_processed: bool = False  # has been summarized by LLM
    is_published: bool = False  # has been included in a newspaper article
    relevance_score: float = 0.0  # LLM-assigned relevance (0-1)


class GitHubRelease(SQLModel, table=True):
    """A tracked GitHub repository release."""

    __tablename__ = "github_releases"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo: str = Field(index=True)  # e.g. "ollama/ollama"
    tag: str
    name: str = ""
    body: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_processed: bool = False
    is_published: bool = False
    guid: str = Field(unique=True, index=True)  # "repo:tag" dedup


# ─── Newspaper Models ─────────────────────────────────────

class Article(SQLModel, table=True):
    """A newspaper article for titanflow.space."""

    __tablename__ = "articles"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    slug: str = Field(index=True)
    content_html: str = ""
    content_markdown: str = ""
    excerpt: str = ""
    category: str = "general"  # model_releases, tools, research, industry, trending, from_the_array
    article_type: str = "briefing"  # briefing, digest, weekly, breaking, trending
    status: str = "draft"  # draft, published, failed
    ghost_post_id: str = ""  # Ghost CMS post ID after publishing
    source_item_ids: str = ""  # comma-separated FeedItem/GitHubRelease IDs
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = None


# ─── Conversation / Memory Models ────────────────────────

class Conversation(SQLModel, table=True):
    """A chat thread (Telegram chat)."""

    __tablename__ = "conversations"

    chat_id: str = Field(primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    role: str = "user"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Message(SQLModel, table=True):
    """A single chat message."""

    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: str = Field(index=True)
    role: str
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    token_est: int = 0
    meta_json: str = "{}"


class PinnedDirective(SQLModel, table=True):
    """Pinned system directives injected into chat context."""

    __tablename__ = "pinned_directives"

    id: Optional[int] = Field(default=None, primary_key=True)
    scope: str = "global"  # global or chat
    chat_id: str = ""
    role: str = "system"
    text: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Security / General Models ────────────────────────────

class SecurityEvent(SQLModel, table=True):
    """A security event or alert."""

    __tablename__ = "security_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = ""  # opnsense, technitium, hass, ssh
    severity: str = "info"  # info, warning, critical
    title: str = ""
    details: str = ""
    is_notified: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLog(SQLModel, table=True):
    """Audit trail for every command, API call, and code execution."""

    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)  # telegram_cmd, api_call, code_exec, llm_chat
    user_id: Optional[int] = None  # Telegram user ID or None for API
    command: str = ""  # /status, GET /api/health, etc.
    args: str = ""
    result: str = ""  # success, error, denied
    details: str = ""  # truncated output or error message
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskLog(SQLModel, table=True):
    """Log of automated task executions."""

    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_name: str = Field(index=True)
    module: str = ""
    status: str = "running"  # running, success, failed
    details: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
