"""Audit sink — appends one row per governed interaction to Postgres.

Uses asyncpg directly (not the registry's ORM) so the gateways stay DB-light and
decoupled. The table is created on first use. Records are *also* emitted as OTel
spans by the PEP, so a single trace ties the whole call chain together.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg

from fabric_common.models import AuditRecord

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id            UUID PRIMARY KEY,
    trace_id      TEXT,
    ts            TIMESTAMPTZ NOT NULL,
    protocol      TEXT NOT NULL,
    action        TEXT NOT NULL,
    subject_sub   TEXT NOT NULL,
    target        TEXT,
    domain        TEXT,
    allowed       BOOLEAN NOT NULL,
    reason        TEXT,
    arg_keys      JSONB,
    data_classes  JSONB,
    latency_ms    DOUBLE PRECISION,
    status        TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);
"""


def _to_asyncpg_dsn(database_url: str) -> str:
    # SQLAlchemy-style "postgresql+asyncpg://..." -> asyncpg "postgresql://..."
    return database_url.replace("+asyncpg", "")


class AuditSink:
    def __init__(self, database_url: str) -> None:
        self._dsn = _to_asyncpg_dsn(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)
        logger.info("Audit sink ready")

    async def write(self, rec: AuditRecord) -> None:
        if self._pool is None:
            logger.warning("audit sink not started; dropping record")
            return
        ts = rec.timestamp or datetime.now(timezone.utc)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO audit_log (id, trace_id, ts, protocol, action, subject_sub,
                       target, domain, allowed, reason, arg_keys, data_classes, latency_ms,
                       status, error)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,$15)""",
                    rec.id, rec.trace_id, ts, rec.protocol, rec.action, rec.subject_sub,
                    rec.target, rec.domain, rec.allowed, rec.reason,
                    _json(rec.arg_keys), _json(rec.data_classes), rec.latency_ms,
                    rec.status, rec.error,
                )
        except Exception as exc:  # pragma: no cover - never fail a request on audit error
            logger.warning("audit write failed: %s", exc)

    async def recent(self, limit: int = 50) -> list[dict]:
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, trace_id, ts, protocol, action, subject_sub, target, allowed, "
                "reason, status FROM audit_log ORDER BY ts DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def _json(value) -> str:
    import json

    return json.dumps(value or [])
