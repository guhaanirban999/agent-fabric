"""ORM model for a registry entry. Mirrors fabric_common.models.AgentEntry."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from registry_svc.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentEntryORM(Base):
    __tablename__ = "agent_entries"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[str] = mapped_column(String(64), default="0.0.0")
    description: Mapped[str] = mapped_column(Text, default="")

    domain: Mapped[str] = mapped_column(String(128), default="default", index=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)

    endpoint_url: Mapped[str] = mapped_column(Text)
    transport: Mapped[str] = mapped_column(String(32))
    auth: Mapped[dict] = mapped_column(JSONB, default=dict)

    agent_card: Mapped[dict] = mapped_column(JSONB, default=dict)
    skills: Mapped[list] = mapped_column(JSONB, default=list)

    card_jws: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)

    health: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    registered_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
