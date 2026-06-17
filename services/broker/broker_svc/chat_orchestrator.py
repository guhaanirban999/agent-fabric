"""Conversational orchestration for the chat frontend.

Per turn (one `broker.chat` span): load history -> route WITH context (auto, so
smalltalk needs no tool) -> dispatch best-first through the gateways (governed) ->
synthesize a natural-language reply from history + message + tool result -> persist.

Reuses Router, the shared dispatch_best_first helper, RegistryClient. Auto-routes
(domain=None) over all healthy candidates.
"""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic

from fabric_common.models import ChatResponse, HealthStatus
from fabric_common.registry_client import RegistryClient
from fabric_common.telemetry import get_tracer
from fabric_common.telemetry.otel import current_trace_id_hex
from broker_svc.conversation_store import ConversationStore
from broker_svc.dispatch import dispatch_best_first
from broker_svc.router import Router

logger = logging.getLogger(__name__)
tracer = get_tracer("broker")

_SYNTH_SYSTEM = (
    "You are a helpful assistant in a Slack chat, fronting an enterprise 'agent fabric' "
    "that can call internal tools/APIs. Reply in a friendly, concise way suitable for "
    "chat. If a TOOL RESULT is provided, answer the user's question using it in natural "
    "language — do not dump raw JSON. If no tool was used, just respond conversationally."
)


class ChatOrchestrator:
    def __init__(
        self,
        registry: RegistryClient,
        router: Router,
        conversations: ConversationStore,
        gateway_mcp_url: str,
        gateway_a2a_url: str,
        *,
        api_key: str,
        model: str,
        history_turns: int = 8,
    ) -> None:
        self._registry = registry
        self._router = router
        self._conv = conversations
        self._gateway_mcp_url = gateway_mcp_url
        self._gateway_a2a_url = gateway_a2a_url
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._history_turns = history_turns

    async def chat(self, session_id: str, message: str) -> ChatResponse:
        with tracer.start_as_current_span("broker.chat") as span:
            trace_id = current_trace_id_hex()
            history = await self._conv.history(session_id, self._history_turns)

            candidates = await self._registry.list_agents(domain=None, health=HealthStatus.UP)
            span.set_attribute("chat.candidate_count", len(candidates))

            # Auto, history-aware routing: returns [] for smalltalk / no-tool messages.
            routes = await self._router.route(message, candidates, history=history, force=False)

            tool_result = None
            decision = None
            used_tool = False
            if routes:
                outcome = await dispatch_best_first(
                    candidates, routes, message, self._gateway_mcp_url, self._gateway_a2a_url
                )
                if outcome.ok:
                    used_tool = True
                    tool_result = outcome.result
                    decision = outcome.route
                    span.set_attribute("chat.selected_agent", outcome.cand.name)
                else:
                    # Tool was indicated but failed — let synthesis explain gracefully.
                    tool_result = {"error": outcome.error}
            span.set_attribute("chat.used_tool", used_tool)

            reply = await self._synthesize(history, message, tool_result, used_tool)

            await self._conv.append(session_id, "user", message)
            await self._conv.append(
                session_id,
                "assistant",
                reply,
                used_tool=used_tool,
                decision=decision.model_dump(mode="json") if decision else None,
                trace_id=trace_id,
            )
            return ChatResponse(
                reply=reply, used_tool=used_tool, decision=decision, trace_id=trace_id
            )

    async def _synthesize(
        self, history: list[dict], message: str, tool_result, used_tool: bool
    ) -> str:
        messages = [{"role": t["role"], "content": t["content"]} for t in history]
        if used_tool:
            content = (
                f"{message}\n\n[TOOL RESULT]\n{tool_result}\n\n"
                "Answer my question using the tool result above."
            )
        else:
            content = message
        messages.append({"role": "user", "content": content})

        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYNTH_SYSTEM,
            messages=messages,
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip() or "(no response)"
