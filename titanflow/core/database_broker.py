"""Database broker: Core-only DB access (async via threadpool)."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanflow.core.config import DatabaseSettings

logger = logging.getLogger("titanflow.db")

# Only allow simple alphanumeric + underscore identifiers
_SAFE_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Schema for v0.2 tables — Core creates these on startup
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    user_id INTEGER,
    command TEXT DEFAULT '',
    args TEXT DEFAULT '',
    result TEXT DEFAULT 'ok',
    details TEXT DEFAULT '{}',
    duration_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feed_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    enabled INTEGER DEFAULT 1,
    last_fetched TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_source_id INTEGER NOT NULL,
    guid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT DEFAULT '',
    author TEXT DEFAULT '',
    content TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    summary TEXT DEFAULT '',
    relevance_score REAL DEFAULT 0.0,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    is_processed INTEGER DEFAULT 0,
    is_published INTEGER DEFAULT 0,
    FOREIGN KEY (feed_source_id) REFERENCES feed_sources(id)
);

CREATE TABLE IF NOT EXISTS github_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    tag TEXT NOT NULL,
    name TEXT DEFAULT '',
    body TEXT DEFAULT '',
    url TEXT DEFAULT '',
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    is_processed INTEGER DEFAULT 0,
    is_published INTEGER DEFAULT 0,
    guid TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS research_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_item_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (feed_item_id) REFERENCES feed_items(id)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    slug TEXT DEFAULT '',
    content_html TEXT DEFAULT '',
    content_markdown TEXT DEFAULT '',
    excerpt TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    article_type TEXT DEFAULT 'briefing',
    status TEXT DEFAULT 'draft',
    ghost_post_id TEXT DEFAULT '',
    source_item_ids TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    chat_id TEXT PRIMARY KEY,
    user_id INTEGER,
    role TEXT DEFAULT 'user',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts TEXT NOT NULL,
    token_est INTEGER DEFAULT 0,
    meta_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS pinned_directives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT DEFAULT 'global',
    chat_id TEXT DEFAULT '',
    role TEXT DEFAULT 'system',
    text TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feed_items_processed ON feed_items(is_processed);
CREATE INDEX IF NOT EXISTS idx_feed_items_relevance ON feed_items(relevance_score);
CREATE INDEX IF NOT EXISTS idx_feed_items_guid ON feed_items(guid);
CREATE INDEX IF NOT EXISTS idx_github_releases_guid ON github_releases(guid);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at);
CREATE INDEX IF NOT EXISTS idx_pinned_directives_scope ON pinned_directives(scope, chat_id, is_active);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
"""


def _validate_identifier(name: str) -> str:
    """Reject anything that isn't a safe SQL identifier."""
    if not _SAFE_IDENT.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


class DatabaseBroker:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Reuse a single connection (thread-safe via to_thread serialization)."""
        if self._conn is None:
            Path(self.settings.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self.settings.path, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}"
            )
            if self.settings.wal_mode:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            logger.info(
                "Database opened: %s (WAL=%s)", self.settings.path, self.settings.wal_mode
            )
        return self._conn

    async def init_schema(self) -> None:
        """Create all v0.2 tables if they don't exist."""
        def _run() -> None:
            conn = self._get_conn()
            conn.executescript(_SCHEMA_SQL)
            logger.info("Database schema initialized")

        await asyncio.to_thread(_run)

    async def query(
        self,
        table: str,
        sql: str,
        params: list[Any] | None = None,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        _validate_identifier(table)
        params = params or []

        def _run() -> list[dict[str, Any]]:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            rows = cur.fetchmany(max_rows or self.settings.max_rows_per_query)
            return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def insert(self, table: str, data: dict[str, Any]) -> int:
        _validate_identifier(table)
        for col in data:
            _validate_identifier(col)

        def _run() -> int:
            cols = ",".join(data.keys())
            placeholders = ",".join(["?"] * len(data))
            sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
            conn = self._get_conn()
            cur = conn.execute(sql, list(data.values()))
            conn.commit()
            return int(cur.lastrowid)

        return await asyncio.to_thread(_run)

    async def update(
        self,
        table: str,
        data: dict[str, Any],
        where: str,
        params: list[Any] | None = None,
    ) -> int:
        _validate_identifier(table)
        for col in data:
            _validate_identifier(col)
        params = params or []

        def _run() -> int:
            set_clause = ",".join([f"{k}=?" for k in data.keys()])
            sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
            conn = self._get_conn()
            cur = conn.execute(sql, list(data.values()) + params)
            conn.commit()
            return cur.rowcount

        return await asyncio.to_thread(_run)

    async def upsert_conversation(self, chat_id: str, user_id: int | None, role: str) -> None:
        def _run() -> None:
            conn = self._get_conn()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO conversations (chat_id, user_id, role, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, role, now, now),
            )
            conn.execute(
                "UPDATE conversations SET user_id = ?, role = ?, last_seen_at = ? WHERE chat_id = ?",
                (user_id, role, now, chat_id),
            )
            conn.commit()

        await asyncio.to_thread(_run)

    async def insert_message(
        self,
        chat_id: str,
        role: str,
        text: str,
        *,
        token_est: int = 0,
        meta_json: str = "{}",
    ) -> int:
        def _run() -> int:
            conn = self._get_conn()
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "INSERT INTO messages (chat_id, role, text, ts, token_est, meta_json) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, role, text, now, token_est, meta_json),
            )
            conn.commit()
            return int(cur.lastrowid)

        return await asyncio.to_thread(_run)

    async def fetch_messages(self, chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT role, text, ts FROM messages WHERE chat_id = ? ORDER BY ts DESC LIMIT ?",
                (chat_id, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]

        return await asyncio.to_thread(_run)

    async def fetch_pinned_directives(self, chat_id: str) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT role, text FROM pinned_directives WHERE is_active = 1 AND (scope = 'global' OR chat_id = ?)",
                (chat_id,),
            )
            return [dict(r) for r in cur.fetchall()]

        return await asyncio.to_thread(_run)

    async def search(self, text: str, limit: int = 6) -> list[dict[str, Any]]:
        terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", text)]
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

        params = feed_params + summary_params + article_params + [limit]

        def _run() -> list[dict[str, Any]]:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
