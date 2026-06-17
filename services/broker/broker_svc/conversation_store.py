"""Per-session conversation memory (asyncpg), so the chat frontend is multi-turn.

Same pattern as store.py / the audit sink. Memory lives in the broker (not the bot)
so any frontend gets it. Keyed by an opaque `session_id` (the Slack bot uses
team:channel:thread for mentions, team:dm:user for DMs).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS broker_conversations (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,          -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    used_tool   BOOLEAN,
    decision    JSONB,
    trace_id    TEXT,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS broker_conv_session_idx
    ON broker_conversations (session_id, created_at DESC);
"""


def _to_asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("+asyncpg", "")


class ConversationStore:
    def __init__(self, database_url: str) -> None:
        self._dsn = _to_asyncpg_dsn(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)
        logger.info("Conversation store ready")

    async def append(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        used_tool: bool | None = None,
        decision: dict | None = None,
        trace_id: str | None = None,
    ) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO broker_conversations
                   (session_id, role, content, used_tool, decision, trace_id, created_at)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)""",
                session_id,
                role,
                content,
                used_tool,
                json.dumps(decision) if decision is not None else None,
                trace_id,
                datetime.now(timezone.utc),
            )

    async def history(self, session_id: str, turns: int = 8) -> list[dict]:
        """Return the last `turns` exchanges (~2*turns rows) in chronological order."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT role, content FROM broker_conversations
                   WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2""",
                session_id,
                turns * 2,
            )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
