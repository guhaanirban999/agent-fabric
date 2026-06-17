"""Registry HTTP API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from fabric_common.models import AgentEntry, RegisterRequest
from registry_svc import repository, service
from registry_svc.db import get_session

router = APIRouter()


@router.post("/agents", response_model=AgentEntry, status_code=201)
async def register_agent(
    req: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> AgentEntry:
    try:
        return await service.register(session, req)
    except service.RegistrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Upstream fetch/introspection failures are surfaced as 502.
        raise HTTPException(status_code=502, detail=f"registration failed: {exc}") from exc


@router.get("/agents", response_model=list[AgentEntry])
async def list_agents(
    domain: str | None = Query(None),
    kind: str | None = Query(None),
    health: str | None = Query(None),
    skill: str | None = Query(None),
    tag: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[AgentEntry]:
    return await repository.list_entries(
        session, domain=domain, kind=kind, health=health, skill=skill, tag=tag
    )


@router.get("/agents/{agent_id}", response_model=AgentEntry)
async def get_agent(agent_id: UUID, session: AsyncSession = Depends(get_session)) -> AgentEntry:
    entry = await repository.get_entry(session, agent_id)
    if not entry:
        raise HTTPException(status_code=404, detail="agent not found")
    return entry


@router.get("/agents/{agent_id}/card")
async def get_agent_card(agent_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    entry = await repository.get_entry(session, agent_id)
    if not entry:
        raise HTTPException(status_code=404, detail="agent not found")
    return entry.agent_card


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    if not await repository.delete_entry(session, agent_id):
        raise HTTPException(status_code=404, detail="agent not found")


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    if not await repository.heartbeat(session, agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    return {"status": "up"}


@router.get("/domains", response_model=list[str])
async def list_domains(session: AsyncSession = Depends(get_session)) -> list[str]:
    return await repository.list_domains(session)
