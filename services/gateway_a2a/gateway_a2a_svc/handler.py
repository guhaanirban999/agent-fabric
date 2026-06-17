"""A2A reverse proxy with governance.

Parses the inbound JSON-RPC envelope, resolves the target agent from the registry,
enforces policy (OPA + rate limit + audit + span), then forwards to the downstream
A2A endpoint — proxying SSE for `message/stream` without buffering.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from fabric_common.auth import JWTValidator, subject_from_request
from fabric_common.governance import PolicyEnforcementPoint
from fabric_common.models import PolicyInput, Subject
from fabric_common.registry_client import RegistryClient

logger = logging.getLogger(__name__)


def _skill_from_body(body: dict) -> str | None:
    params = body.get("params") or {}
    meta = params.get("metadata") or {}
    return meta.get("skill")


class A2AProxy:
    def __init__(
        self,
        pep: PolicyEnforcementPoint,
        validator: JWTValidator,
        registry: RegistryClient,
    ) -> None:
        self._pep = pep
        self._validator = validator
        self._registry = registry

    def _subject(self, request: Request) -> Subject:
        try:
            return subject_from_request(self._validator, request.headers.get("authorization"))
        except PermissionError:
            return Subject(sub="unauthenticated", scopes=[])

    async def handle(self, agent_id: str, request: Request):
        body = await request.json()
        method = body.get("method", "")

        try:
            entry = await self._registry.get_agent(agent_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return JSONResponse(status_code=404, content={"error": "agent not found"})
            raise

        # Resolve the skill for policy: explicit header > message metadata > first skill.
        skill = (
            request.headers.get("x-fabric-skill")
            or _skill_from_body(body)
            or (entry.skills[0].id if entry.skills else None)
        )

        decision = await self._pep.enforce(
            PolicyInput(
                subject=self._subject(request),
                protocol="a2a",
                action="a2a.message_send",
                server=entry.name,
                skill=skill,
                domain=entry.domain,
            )
        )
        if not decision.allow:
            return JSONResponse(
                status_code=403,
                content={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "error": {"code": -32001, "message": f"policy denied: {decision.reason}"},
                },
            )

        endpoint = str(entry.endpoint_url)
        fwd_headers = self._downstream_headers(request)
        # Streaming methods (gRPC-style SendStreamingMessage / legacy message/stream).
        if method in ("message/stream", "SendStreamingMessage", "SubscribeToTask"):
            return self._stream(endpoint, body, fwd_headers)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(endpoint, json=body, headers=fwd_headers)
        try:
            content = resp.json()
        except Exception:
            content = {"raw": resp.text}
        return JSONResponse(status_code=resp.status_code, content=content)

    @staticmethod
    def _downstream_headers(request: Request) -> dict[str, str]:
        """Propagate protocol headers to the downstream agent. Auth propagation
        (token-exchange / on-behalf-of) is added in Phase 4 — for now we forward the
        A2A protocol version and content negotiation headers."""
        headers = {"content-type": "application/json"}
        for h in ("a2a-version", "accept", "x-fabric-skill"):
            if h in request.headers:
                headers[h] = request.headers[h]
        return headers

    def _stream(self, endpoint: str, body: dict, fwd_headers: dict[str, str]) -> StreamingResponse:
        headers = {**fwd_headers, "accept": "text/event-stream"}

        async def gen():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", endpoint, json=body, headers=headers) as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk

        return StreamingResponse(gen(), media_type="text/event-stream")
