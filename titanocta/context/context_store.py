"""SQLite-backed context store (WAL mode) for per-user/per-session memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ContextEntry:
    id: int
    user_id: str
    session_id: str
    role: str
    content: str
    score: float
    token_estimate: int
    durable: bool
    kind: str
    created_at: str


class ContextStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def add_entry(
        self,
        *,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        score: float = 1.0,
        token_estimate: int | None = None,
        durable: bool = False,
        kind: str = "message",
    ) -> int:
        tokens = token_estimate if token_estimate is not None else max(1, len(content) // 4)
        conn = self._connect()
        cur = conn.execute(
            """
            insert into context_entries (
                user_id, session_id, role, content, score, token_estimate, durable, kind, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                role,
                content,
                float(score),
                int(tokens),
                1 if durable else 0,
                kind,
                _utc_now(),
            ),
        )
        conn.commit()
        entry_id = int(cur.lastrowid)
        conn.close()
        return entry_id

    def list_entries(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int = 50,
        min_score: float = 0.0,
        only_durable: bool = False,
        exclude_kind: str | None = None,
    ) -> list[ContextEntry]:
        conn = self._connect()
        rows = conn.execute(
            """
            select id, user_id, session_id, role, content, score, token_estimate, durable, kind, created_at
            from context_entries
            where user_id = ? and session_id = ? and score >= ? and (? = 0 or durable = 1)
              and (? is null or kind != ?)
            order by id desc
            limit ?
            """,
            (
                user_id,
                session_id,
                float(min_score),
                1 if only_durable else 0,
                exclude_kind,
                exclude_kind,
                int(limit),
            ),
        ).fetchall()
        conn.close()
        return [self._row_to_entry(row) for row in rows]

    def durable_fact_exists(self, *, user_id: str, session_id: str, content: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            """
            select 1 from context_entries
            where user_id = ? and session_id = ? and kind = 'durable_fact' and content = ?
            limit 1
            """,
            (user_id, session_id, content),
        ).fetchone()
        conn.close()
        return row is not None

    def update_score(self, *, entry_id: int, score: float) -> None:
        conn = self._connect()
        conn.execute(
            "update context_entries set score = ? where id = ?",
            (float(score), int(entry_id)),
        )
        conn.commit()
        conn.close()

    def total_tokens(
        self,
        *,
        user_id: str,
        session_id: str,
        only_durable: bool = False,
    ) -> int:
        conn = self._connect()
        row = conn.execute(
            """
            select coalesce(sum(token_estimate), 0)
            from context_entries
            where user_id = ? and session_id = ? and (? = 0 or durable = 1)
            """,
            (user_id, session_id, 1 if only_durable else 0),
        ).fetchone()
        conn.close()
        return int(row[0] if row else 0)

    def journal_mode(self) -> str:
        conn = self._connect()
        row = conn.execute("pragma journal_mode").fetchone()
        conn.close()
        return str(row[0]).lower() if row else ""

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute("pragma journal_mode=WAL")
        conn.execute(
            """
            create table if not exists context_entries (
                id integer primary key autoincrement,
                user_id text not null,
                session_id text not null,
                role text not null,
                content text not null,
                score real not null default 1.0,
                token_estimate integer not null default 0,
                durable integer not null default 0,
                kind text not null default 'message',
                created_at text not null
            )
            """,
        )
        conn.execute(
            """
            create index if not exists idx_context_user_session
            on context_entries(user_id, session_id, id desc)
            """,
        )
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ContextEntry:
        return ContextEntry(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            score=float(row["score"]),
            token_estimate=int(row["token_estimate"]),
            durable=bool(row["durable"]),
            kind=str(row["kind"]),
            created_at=str(row["created_at"]),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
