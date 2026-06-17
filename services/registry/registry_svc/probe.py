"""Active health prober.

Periodically probes every registered endpoint and flips its `health`. The broker
excludes non-UP entries from candidate sets. Agents may also self-report via
`POST /agents/{id}/heartbeat`.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from fabric_common.models import AgentEntry, AgentKind, HealthStatus
from registry_svc import repository
from registry_svc.cards import WELL_KNOWN_PATH
from registry_svc.db import SessionLocal
from registry_svc.settings import get_registry_settings

logger = logging.getLogger(__name__)
settings = get_registry_settings()


async def probe_entry(entry: AgentEntry) -> HealthStatus:
    try:
        if entry.kind == AgentKind.MCP_SERVER:
            from fastmcp import Client

            async with Client(str(entry.endpoint_url)) as client:
                await client.list_tools()
            return HealthStatus.UP

        # A2A: the well-known card must be reachable.
        url = str(entry.endpoint_url).rstrip("/")
        if not url.endswith("agent-card.json"):
            url += WELL_KNOWN_PATH
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        return HealthStatus.UP if resp.status_code < 500 else HealthStatus.DOWN
    except Exception as exc:
        logger.debug("probe failed for %s: %s", entry.name, exc)
        return HealthStatus.DOWN


async def _sweep_once() -> None:
    async with SessionLocal() as session:
        entries = await repository.list_entries(session)
        for entry in entries:
            status = await probe_entry(entry)
            await repository.set_health(session, entry.id, status)


async def run_prober(stop: asyncio.Event) -> None:
    interval = settings.registry_heartbeat_interval
    logger.info("Health prober started (interval=%ss)", interval)
    while not stop.is_set():
        try:
            await _sweep_once()
        except Exception as exc:  # pragma: no cover - never let the loop die
            logger.warning("prober sweep error: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
