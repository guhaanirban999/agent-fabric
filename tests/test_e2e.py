"""End-to-end tests: governance at the gateways + broker routing through them."""

from __future__ import annotations

import pytest
from conftest import BROKER, GW_A2A, GW_MCP, find_agent
from fastmcp import Client
from fastmcp.exceptions import ClientError, ToolError


async def test_mcp_gateway_filters_lists_allows_and_denies():
    async with Client(f"{GW_MCP}/mcp") as c:
        tool_names = {t.name for t in await c.list_tools()}
        # Allowed tools are visible; the ungoverned 'danger' tool is filtered out.
        assert {"echo", "reverse", "add"} <= tool_names
        assert "danger" not in tool_names

        result = await c.call_tool("reverse", {"text": "abc"})
        assert result.data == "cba"

        with pytest.raises((ToolError, ClientError)):
            await c.call_tool("danger", {"target": "x"})


async def test_a2a_gateway_denies_forbidden_skill(client):
    a2a = await find_agent(client, "a2a_agent")
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}], "messageId": "m"}},
    }
    resp = await client.post(
        f"{GW_A2A}/a2a/{a2a['id']}",
        json=body,
        headers={"A2A-Version": "1.0", "x-fabric-skill": "forbidden"},
    )
    assert resp.status_code == 403
    assert "denied" in resp.text


async def test_a2a_gateway_allows_echo(client):
    a2a = await find_agent(client, "a2a_agent")
    body = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "SendMessage",
        "params": {
            "message": {"role": "ROLE_USER", "parts": [{"text": "ping"}], "messageId": "m2"}
        },
    }
    resp = await client.post(
        f"{GW_A2A}/a2a/{a2a['id']}", json=body, headers={"A2A-Version": "1.0"}
    )
    assert resp.status_code == 200
    assert "echo: ping" in resp.text


async def test_broker_routes_mcp_task(client):
    resp = await client.post(f"{BROKER}/tasks", json={"task_text": "reverse the word hardening"})
    assert resp.status_code == 200
    task = resp.json()
    assert task["state"] == "completed", task
    assert task["result"] == "gninedrah"


async def test_broker_streams_progress(client):
    """The SSE endpoint emits accepted -> ... -> completed events."""
    events = []
    async with client.stream(
        "POST", f"{BROKER}/tasks/stream", json={"task_text": "add 2 and 3"}
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            if "completed" in events or "failed" in events:
                break
    assert "accepted" in events
    assert "completed" in events
