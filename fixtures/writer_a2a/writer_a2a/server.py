"""Writer A2A agent fixture (a2a-sdk 1.1.x).

A native A2A agent that uses the Anthropic SDK to summarize, rewrite, draft, or
answer free-form text. It publishes an Agent Card at `/.well-known/agent-card.json`
so the registry can discover it and the broker can route to it — giving the fabric a
real multi-protocol routing choice (an MCP tool vs. a reasoning A2A agent).

Run: python -m writer_a2a.server   (serves on 0.0.0.0:9003)

Same a2a-sdk 1.x wiring as echo_a2a (proto types + explicit route builders); the only
difference is the executor calls Anthropic instead of echoing. Requires ANTHROPIC_API_KEY
in the environment (inherited from the compose env_file). Model via WRITER_MODEL.
"""

from __future__ import annotations

import os
from uuid import uuid4

import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
)

RPC_URL = "/"

SYSTEM_PROMPT = (
    "You are a concise, helpful writing assistant. The user may ask you to summarize, "
    "rewrite, draft, or answer free-form text. Respond directly with the result only — "
    "no preamble, no meta-commentary."
)


class WriterExecutor(AgentExecutor):
    def __init__(self) -> None:
        # One client per process; reads ANTHROPIC_API_KEY from the environment.
        self._client = AsyncAnthropic()
        self._model = os.environ.get("WRITER_MODEL", "claude-haiku-4-5")

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input()
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_text}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", None) == "text"
            ).strip()
            if not text:
                text = "(writer-a2a produced no text)"
        except Exception as exc:  # noqa: BLE001 — surface a readable reply, not a 500
            text = f"writer-a2a error: {exc}"

        reply = Message(
            message_id=uuid4().hex,
            role=Role.ROLE_AGENT,
            parts=[Part(text=text)],
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise RuntimeError("cancel not supported by writer agent")


def build_agent_card() -> AgentCard:
    public_url = os.environ.get("A2A_PUBLIC_URL", "http://localhost:9003/")
    return AgentCard(
        name="writer-a2a",
        description="An LLM-backed A2A agent that summarizes, rewrites, drafts, or answers text.",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC"),
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id="assist",
                name="Writing Assistant",
                description="Summarize, rewrite, draft, or answer free-form text requests.",
                tags=["text", "llm", "assistant"],
                examples=[
                    "summarize this paragraph",
                    "rewrite this more formally",
                    "answer: what is a control plane?",
                ],
            ),
        ],
    )


def build_app() -> FastAPI:
    card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=WriterExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title="writer-a2a")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, RPC_URL),
    )
    return app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "9003"))
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
