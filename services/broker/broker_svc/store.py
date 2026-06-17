"""Task persistence (asyncpg, decoupled from the registry ORM)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg

from fabric_common.models import TaskRecord

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS broker_tasks (
    id          UUID PRIMARY KEY,
    submission  JSONB NOT NULL,
    state       TEXT NOT NULL,
    domain      TEXT,
    decision    JSONB,
    result      JSONB,
    error       TEXT,
    trace_id    TEXT,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS broker_tasks_created_idx ON broker_tasks (created_at DESC);
"""


def _to_asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("+asyncpg", "")


class TaskStore:
    def __init__(self, database_url: str) -> None:
        self._dsn = _to_asyncpg_dsn(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)
        logger.info("Task store ready")

    async def upsert(self, task: TaskRecord) -> None:
        assert self._pool is not None
        now = datetime.now(timezone.utc)
        created = task.created_at or now
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO broker_tasks
                   (id, submission, state, domain, decision, result, error, trace_id,
                    created_at, updated_at)
                   VALUES ($1,$2::jsonb,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10)
                   ON CONFLICT (id) DO UPDATE SET
                     state=EXCLUDED.state, domain=EXCLUDED.domain, decision=EXCLUDED.decision,
                     result=EXCLUDED.result, error=EXCLUDED.error, trace_id=EXCLUDED.trace_id,
                     updated_at=EXCLUDED.updated_at""",
                task.id,
                task.submission.model_dump_json(),
                task.state.value,
                task.domain,
                json.dumps(task.decision.model_dump(mode="json")) if task.decision else None,
                json.dumps(task.result) if task.result is not None else None,
                task.error,
                task.trace_id,
                created,
                now,
            )

    async def get(self, task_id: str) -> dict | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM broker_tasks WHERE id = $1::uuid", task_id)
        return _row_to_dict(row) if row else None

    async def list(self, limit: int = 50) -> list[dict]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM broker_tasks ORDER BY created_at DESC LIMIT $1", limit
            )
        return [_row_to_dict(r) for r in rows]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("submission", "decision", "result"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    for k in ("id", "created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d
