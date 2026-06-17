"""Shared fixtures. Tests run INSIDE the compose network, so defaults use service names.

Run them with:
    docker compose run --rm -v "$PWD/tests:/tests" --no-deps broker \
        bash -lc "uv pip install -q pytest pytest-asyncio && python -m pytest /tests"
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

REGISTRY = os.environ.get("REGISTRY_URL", "http://registry:8000")
GW_MCP = os.environ.get("GATEWAY_MCP_URL", "http://gateway-mcp:8000")
GW_A2A = os.environ.get("GATEWAY_A2A_URL", "http://gateway-a2a:8000")
BROKER = os.environ.get("BROKER_URL", "http://broker:8000")


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(timeout=60.0) as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def ensure_fixtures_registered(client):
    """Idempotently ensure the two fixtures are registered (no-op if already present)."""
    agents = (await client.get(f"{REGISTRY}/agents")).json()
    kinds = {a["kind"] for a in agents}
    if "mcp_server" not in kinds:
        await client.post(
            f"{REGISTRY}/agents",
            json={
                "kind": "mcp_server",
                "name": "echo-mcp",
                "domain": "demo",
                "endpoint_url": "http://echo-mcp:9001/mcp",
                "transport": "streamable-http",
            },
        )
    if "a2a_agent" not in kinds:
        await client.post(
            f"{REGISTRY}/agents",
            json={"kind": "a2a_agent", "domain": "demo", "card_url": "http://echo-a2a:9002"},
        )
    # The LLM writing-assistant A2A agent (registered by skill, since echo-a2a is also a2a_agent).
    have_assist = any(any(s["id"] == "assist" for s in a["skills"]) for a in agents)
    if not have_assist:
        await client.post(
            f"{REGISTRY}/agents",
            json={"kind": "a2a_agent", "domain": "assistant", "card_url": "http://writer-a2a:9003"},
        )


async def find_agent(client, kind: str) -> dict:
    agents = (await client.get(f"{REGISTRY}/agents", params={"kind": kind})).json()
    assert agents, f"no agent of kind {kind} registered"
    return agents[0]


async def find_agent_by_skill(client, skill: str) -> dict:
    agents = (await client.get(f"{REGISTRY}/agents", params={"skill": skill})).json()
    assert agents, f"no agent exposing skill {skill} registered"
    return agents[0]
