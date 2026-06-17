"""Dispatch a routed task to a downstream agent/tool — always THROUGH a gateway,
never directly, so governance (policy + audit + tracing) always applies.

MCP -> fastmcp client against gateway-mcp's composite /mcp endpoint.
A2A -> JSON-RPC SendMessage against gateway-a2a's /a2a/{agent_id} endpoint.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from fabric_common.models import AgentEntry, AgentKind, RouteDecision

logger = logging.getLogger(__name__)


@dataclass
class DispatchOutcome:
    cand: AgentEntry | None = None
    route: RouteDecision | None = None
    result: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.cand is not None


async def dispatch_best_first(
    candidates: list[AgentEntry],
    routes: list[RouteDecision],
    text: str,
    gateway_mcp_url: str,
    gateway_a2a_url: str,
    *,
    on_attempt: Callable[[AgentEntry, RouteDecision], Awaitable[None]] | None = None,
    on_retry: Callable[[AgentEntry, str], Awaitable[None]] | None = None,
) -> DispatchOutcome:
    """Try routes best-first, retrying the next on failure. Every dispatch goes through
    a governed gateway (dispatch_mcp / dispatch_a2a) — the single enforcement path."""
    by_id = {str(c.id): c for c in candidates}
    last_error = "no route succeeded"
    for route in routes:
        cand = by_id.get(route.agent_id)
        if cand is None:
            continue
        if on_attempt:
            await on_attempt(cand, route)
        try:
            if cand.kind == AgentKind.MCP_SERVER:
                result = await dispatch_mcp(gateway_mcp_url, route.skill_id, route.arguments)
            else:
                msg = route.arguments.get("text") or text
                result = await dispatch_a2a(gateway_a2a_url, route.agent_id, route.skill_id, msg)
        except DispatchError as exc:
            last_error = str(exc)
            if on_retry:
                await on_retry(cand, str(exc))
            continue
        return DispatchOutcome(cand=cand, route=route, result=result)
    return DispatchOutcome(error=last_error)


def _mcp_result(result) -> Any:
    """Normalize a FastMCP call result. Prefer structured `.data`; otherwise fall back
    to text content (many tools, incl. MuleSoft's, return a JSON string as text) and
    parse it as JSON when possible."""
    if getattr(result, "data", None) is not None:
        return result.data
    blocks = getattr(result, "content", None) or []
    texts = [getattr(b, "text", "") for b in blocks if getattr(b, "text", None)]
    text = "\n".join(texts).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


class DispatchError(RuntimeError):
    pass


async def dispatch_mcp(gateway_mcp_url: str, tool: str, arguments: dict) -> Any:
    """Call an MCP tool through the governed composite endpoint."""
    from fastmcp import Client

    endpoint = f"{gateway_mcp_url.rstrip('/')}/mcp"
    try:
        async with Client(endpoint) as client:
            result = await client.call_tool(tool, arguments or {})
    except Exception as exc:
        raise DispatchError(f"mcp dispatch failed for tool={tool}: {exc}") from exc
    return _mcp_result(result)


async def dispatch_a2a(
    gateway_a2a_url: str, agent_id: str, skill_id: str | None, text: str
) -> Any:
    """Send an A2A message through the governed gateway (gRPC-style SendMessage)."""
    body = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": uuid4().hex,
            }
        },
    }
    headers = {
        "content-type": "application/json",
        "A2A-Version": "1.0",
        "x-fabric-skill": skill_id or "",
    }
    url = f"{gateway_a2a_url.rstrip('/')}/a2a/{agent_id}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=headers)
    if resp.status_code == 403:
        raise DispatchError(f"policy denied at gateway: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise DispatchError(f"a2a error: {data['error']}")
    return _extract_text(data.get("result", data))


def _extract_text(obj: Any) -> Any:
    """Pull the first text part out of an A2A Message/Task response."""
    texts: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("text"), str):
                texts.append(o["text"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return texts[0] if texts else obj
