"""Thin client for the broker's conversational endpoint."""

from __future__ import annotations

import httpx


class BrokerClient:
    def __init__(self, base_url: str, timeout: float = 90.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def chat(self, session_id: str, message: str) -> str:
        resp = await self._client.post(
            "/chat", json={"session_id": session_id, "message": message}
        )
        resp.raise_for_status()
        return resp.json().get("reply", "(no reply)")

    async def aclose(self) -> None:
        await self._client.aclose()
