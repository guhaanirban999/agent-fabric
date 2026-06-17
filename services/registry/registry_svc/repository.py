"""Persistence layer: CRUD over AgentEntryORM, mapping to/from fabric_common models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fabric_common.models import AgentEntry, AgentSkillSummary, HealthStatus
from registry_svc.orm import AgentEntryORM


def orm_to_model(row: AgentEntryORM) -> AgentEntry:
    return AgentEntry(
        id=row.id,
        kind=row.kind,
        name=row.name,
        version=row.version,
        description=row.description,
        domain=row.domain,
        tags=row.tags or [],
        endpoint_url=row.endpoint_url,
        transport=row.transport,
        auth=row.auth or {},
        agent_card=row.agent_card or {},
        skills=[AgentSkillSummary.model_validate(s) for s in (row.skills or [])],
        card_jws=row.card_jws,
        trusted=row.trusted,
        health=row.health,
        last_heartbeat=row.last_heartbeat,
        registered_by=row.registered_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def model_to_orm_kwargs(entry: AgentEntry) -> dict:
    return dict(
        id=entry.id,
        kind=entry.kind.value,
        name=entry.name,
        version=entry.version,
        description=entry.description,
        domain=entry.domain,
        tags=entry.tags,
        endpoint_url=str(entry.endpoint_url),
        transport=entry.transport.value,
        auth=entry.auth.model_dump(mode="json"),
        agent_card=entry.agent_card,
        skills=[s.model_dump() for s in entry.skills],
        card_jws=entry.card_jws,
        trusted=entry.trusted,
        health=entry.health.value,
        last_heartbeat=entry.last_heartbeat,
        registered_by=entry.registered_by,
    )


async def create_entry(session: AsyncSession, entry: AgentEntry) -> AgentEntry:
    row = AgentEntryORM(**model_to_orm_kwargs(entry))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return orm_to_model(row)


async def get_entry(session: AsyncSession, entry_id: UUID) -> AgentEntry | None:
    row = await session.get(AgentEntryORM, entry_id)
    return orm_to_model(row) if row else None


async def delete_entry(session: AsyncSession, entry_id: UUID) -> bool:
    row = await session.get(AgentEntryORM, entry_id)
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_entries(
    session: AsyncSession,
    *,
    domain: str | None = None,
    kind: str | None = None,
    health: str | None = None,
    skill: str | None = None,
    tag: str | None = None,
) -> list[AgentEntry]:
    stmt = select(AgentEntryORM)
    if domain:
        stmt = stmt.where(AgentEntryORM.domain == domain)
    if kind:
        stmt = stmt.where(AgentEntryORM.kind == kind)
    if health:
        stmt = stmt.where(AgentEntryORM.health == health)
    rows = (await session.execute(stmt)).scalars().all()
    entries = [orm_to_model(r) for r in rows]

    # JSONB-array filters applied in Python (small N for a pilot).
    if skill:
        entries = [e for e in entries if any(s.id == skill for s in e.skills)]
    if tag:
        entries = [e for e in entries if tag in e.tags]
    return entries


async def heartbeat(session: AsyncSession, entry_id: UUID) -> bool:
    row = await session.get(AgentEntryORM, entry_id)
    if not row:
        return False
    row.health = HealthStatus.UP.value
    row.last_heartbeat = datetime.now(timezone.utc)
    await session.commit()
    return True


async def set_health(session: AsyncSession, entry_id: UUID, status: HealthStatus) -> None:
    row = await session.get(AgentEntryORM, entry_id)
    if row:
        row.health = status.value
        if status == HealthStatus.UP:
            row.last_heartbeat = datetime.now(timezone.utc)
        await session.commit()


async def list_domains(session: AsyncSession) -> list[str]:
    stmt = select(AgentEntryORM.domain).distinct()
    return sorted({d for (d,) in (await session.execute(stmt)).all()})
