"""Registration orchestration: turn a RegisterRequest into a persisted AgentEntry."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from fabric_common.models import (
    AgentEntry,
    AgentKind,
    HealthStatus,
    RegisterRequest,
    Transport,
)
from registry_svc import cards, repository
from registry_svc.settings import get_registry_settings

logger = logging.getLogger(__name__)
settings = get_registry_settings()


class RegistrationError(ValueError):
    pass


async def register(session: AsyncSession, req: RegisterRequest) -> AgentEntry:
    if req.kind == AgentKind.MCP_SERVER:
        return await _register_mcp(session, req)
    return await _register_a2a(session, req)


async def _register_a2a(session: AsyncSession, req: RegisterRequest) -> AgentEntry:
    if not req.card_url:
        raise RegistrationError("card_url is required for A2A registration")
    card_dict = await cards.fetch_a2a_card(req.card_url)
    card = cards.parse_card(card_dict)
    endpoint = cards.endpoint_from_card(card, card_dict)
    if not endpoint:
        raise RegistrationError("card has no resolvable endpoint URL")

    trusted = cards.verify_card_signature(card, settings.registry_trusted_jwks)
    if settings.registry_require_signed and not trusted:
        raise RegistrationError("card signature missing/invalid and signed cards are required")

    entry = AgentEntry(
        kind=req.kind,
        name=card.name or "a2a-agent",
        version=card.version or "0.0.0",
        description=card.description or "",
        domain=req.domain,
        tags=req.tags,
        endpoint_url=endpoint,
        transport=req.transport or Transport.JSONRPC,
        auth=req.auth,
        agent_card=card_dict,
        skills=cards.skills_from_card(card),
        trusted=trusted,
        health=HealthStatus.UNKNOWN,
        registered_by=req.registered_by,
    )
    return await repository.create_entry(session, entry)


async def _register_mcp(session: AsyncSession, req: RegisterRequest) -> AgentEntry:
    if not req.endpoint_url:
        raise RegistrationError("endpoint_url is required for MCP registration")
    name = req.name or _host_of(req.endpoint_url)
    synthetic_card, skills = await cards.introspect_mcp(req.endpoint_url, name_hint=name)

    entry = AgentEntry(
        kind=req.kind,
        name=name,
        version="0.0.0",
        description=synthetic_card.get("description", ""),
        domain=req.domain,
        tags=req.tags or ["mcp"],
        endpoint_url=req.endpoint_url,
        transport=req.transport or Transport.STREAMABLE_HTTP,
        auth=req.auth,
        agent_card=synthetic_card,
        skills=skills,
        trusted=False,  # MCP servers carry no A2A signature
        health=HealthStatus.UNKNOWN,
        registered_by=req.registered_by,
    )
    return await repository.create_entry(session, entry)


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc or url
    return f"mcp-{netloc}"
