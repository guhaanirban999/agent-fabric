"""Broker service — domain-scoped registry retrieval + LLM routing + governed dispatch.

POST /tasks      submit a natural-language task -> routed + dispatched -> result
GET  /tasks/{id} fetch a task record
GET  /tasks      list recent tasks
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from fabric_common.config import get_settings
from fabric_common.models import ChatRequest, ChatResponse, TaskSubmission
from fabric_common.registry_client import RegistryClient
from fabric_common.telemetry import setup_telemetry
from broker_svc.chat_orchestrator import ChatOrchestrator
from broker_svc.conversation_store import ConversationStore
from broker_svc.orchestrator import Orchestrator
from broker_svc.router import Router
from broker_svc.store import TaskStore

logging.basicConfig(level=logging.INFO)
settings = get_settings()

store = TaskStore(settings.database_url)
conversations = ConversationStore(settings.database_url)
registry = RegistryClient(settings.registry_url)
router = Router(api_key=settings.anthropic_api_key, model=settings.broker_model)
orchestrator = Orchestrator(
    registry=registry,
    router=router,
    store=store,
    gateway_mcp_url=settings.gateway_mcp_url,
    gateway_a2a_url=settings.gateway_a2a_url,
)
chat_orchestrator = ChatOrchestrator(
    registry=registry,
    router=router,
    conversations=conversations,
    gateway_mcp_url=settings.gateway_mcp_url,
    gateway_a2a_url=settings.gateway_a2a_url,
    api_key=settings.anthropic_api_key,
    model=settings.broker_model,
    history_turns=settings.chat_history_turns,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.start()
    await conversations.start()
    try:
        yield
    finally:
        await store.aclose()
        await conversations.aclose()
        await registry.aclose()


app = FastAPI(title="Agent Fabric — Broker", version="0.1.0", lifespan=lifespan)
setup_telemetry("broker", settings.otel_exporter_otlp_endpoint, fastapi_app=app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "broker"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Multi-turn conversational entrypoint used by chat frontends (e.g. the Slack bot).
    Routes with conversation context, dispatches through the gateways, and synthesizes a
    natural-language reply. Smalltalk gets a conversational answer with no tool call."""
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    return await chat_orchestrator.chat(req.session_id, req.message)


@app.post("/tasks")
async def submit_task(submission: TaskSubmission) -> dict:
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    task = await orchestrator.run(submission)
    return task.model_dump(mode="json")


@app.post("/tasks/stream")
async def submit_task_stream(submission: TaskSubmission):
    """Same pipeline as POST /tasks, but streams progress events over SSE."""
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await orchestrator._execute(submission, emit)
        except Exception as exc:  # surface unexpected errors as a terminal event
            await queue.put({"event": "error", "error": str(exc)})
        finally:
            await queue.put(None)

    asyncio.create_task(runner())

    async def event_source():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield {"event": event.get("event", "message"), "data": json.dumps(event)}

    return EventSourceResponse(event_source())


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    record = await store.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    return record


@app.get("/tasks")
async def list_tasks(limit: int = 50) -> list[dict]:
    return await store.list(limit)
