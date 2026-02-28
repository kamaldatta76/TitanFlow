"""Database broker: Core-only DB access (async via threadpool)."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
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

CREATE INDEX IF NOT EXISTS idx_feed_items_processed ON feed_items(is_processed);
CREATE INDEX IF NOT EXISTS idx_feed_items_relevance ON feed_items(relevance_score);
CREATE INDEX IF NOT EXISTS idx_feed_items_guid ON feed_items(guid);
CREATE INDEX IF NOT EXISTS idx_github_releases_guid ON github_releases(guid);
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

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
