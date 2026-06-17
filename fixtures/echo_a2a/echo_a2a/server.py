"""Echo A2A agent fixture (a2a-sdk 1.1.x).

A minimal native A2A agent that echoes user input and publishes an Agent Card at
`/.well-known/agent-card.json` so the registry can discover it and the broker can
route to it.

Run: python -m echo_a2a.server   (serves on 0.0.0.0:9002)

NOTE on the SDK: a2a-sdk 1.x moved its wire types to **protobuf** (`a2a.types` are
proto messages, not pydantic) and replaced `A2AStarletteApplication` with explicit
route builders (`create_jsonrpc_routes` / `create_agent_card_routes` +
`add_a2a_routes_to_fastapi`). The agent endpoint is advertised via
`supported_interfaces` rather than a single `url`. This churn is exactly why the
PLAN wraps SDK calls behind adapters.
"""

from __future__ import annotations

import os

import uvicorn
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


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Respond with a direct agent Message (simpler than the Task lifecycle and
        # sufficient for an echo). The message_id just needs to be unique per reply.
        from uuid import uuid4

        user_text = context.get_user_input()
        reply = Message(
            message_id=uuid4().hex,
            role=Role.ROLE_AGENT,
            parts=[Part(text=f"echo: {user_text}")],
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Echo is instantaneous; nothing to cancel.
        raise RuntimeError("cancel not supported by echo agent")


def build_agent_card() -> AgentCard:
    public_url = os.environ.get("A2A_PUBLIC_URL", "http://localhost:9002/")
    return AgentCard(
        name="echo-a2a",
        description="A trivial A2A agent that echoes the user's text back.",
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC"),
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echoes the user's message back, prefixed with 'echo:'.",
                tags=["echo", "text", "demo"],
                examples=["say hello", "repeat this back to me"],
            ),
        ],
    )


def build_app() -> FastAPI:
    card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title="echo-a2a")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, RPC_URL),
    )
    return app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "9002"))
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
