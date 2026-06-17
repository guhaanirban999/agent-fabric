"""Contract tests: the registry's stored descriptors match the expected shape."""

from __future__ import annotations

from conftest import REGISTRY, find_agent

REQUIRED_ENTRY_FIELDS = {"id", "kind", "name", "endpoint_url", "transport", "skills", "agent_card"}


async def test_entries_have_required_fields(client):
    agents = (await client.get(f"{REGISTRY}/agents")).json()
    assert isinstance(agents, list) and agents
    for a in agents:
        missing = REQUIRED_ENTRY_FIELDS - set(a)
        assert not missing, f"{a.get('name')} missing fields: {missing}"


async def test_mcp_synthetic_card_exposes_tools(client):
    mcp = await find_agent(client, "mcp_server")
    skill_ids = {s["id"] for s in mcp["skills"]}
    assert {"echo", "reverse", "add"} <= skill_ids, skill_ids
    # The synthetic card mirrors the skills.
    card_skill_ids = {s["id"] for s in mcp["agent_card"]["skills"]}
    assert {"echo", "reverse", "add"} <= card_skill_ids


async def test_a2a_card_has_echo_skill(client):
    a2a = await find_agent(client, "a2a_agent")
    assert a2a["agent_card"].get("name")
    skill_ids = {s["id"] for s in a2a["skills"]}
    assert "echo" in skill_ids


async def test_discovery_filters(client):
    by_skill = (await client.get(f"{REGISTRY}/agents", params={"skill": "reverse"})).json()
    assert by_skill and all(
        any(s["id"] == "reverse" for s in a["skills"]) for a in by_skill
    )
    a2a_only = (await client.get(f"{REGISTRY}/agents", params={"kind": "a2a_agent"})).json()
    assert a2a_only and all(a["kind"] == "a2a_agent" for a in a2a_only)


async def test_domains_endpoint(client):
    domains = (await client.get(f"{REGISTRY}/domains")).json()
    assert "demo" in domains
