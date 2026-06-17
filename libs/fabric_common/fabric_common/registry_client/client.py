"""Typed async client for the registry service. Used by the gateways and the broker."""

from __future__ import annotations

import httpx

from fabric_common.models import AgentEntry, HealthStatus


class RegistryClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def list_agents(
        self,
        *,
        domain: str | None = None,
        skill: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
        health: HealthStatus | None = None,
    ) -> list[AgentEntry]:
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if skill:
            params["skill"] = skill
        if kind:
            params["kind"] = kind
        if tag:
            params["tag"] = tag
        if health:
            params["health"] = health.value
        resp = await self._client.get("/agents", params=params)
        resp.raise_for_status()
        return [AgentEntry.model_validate(item) for item in resp.json()]

    async def get_agent(self, agent_id: str) -> AgentEntry:
        resp = await self._client.get(f"/agents/{agent_id}")
        resp.raise_for_status()
        return AgentEntry.model_validate(resp.json())

    async def list_domains(self) -> list[str]:
        resp = await self._client.get("/domains")
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
