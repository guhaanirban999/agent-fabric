"""Registry service — agent/MCP/A2A catalog + discovery + health.

Phase 1: full catalog. On startup it creates the schema (dev convenience; the
Alembic migration in `alembic/` is the production path) and launches the health
prober.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fabric_common.config import get_settings
from fabric_common.telemetry import setup_telemetry
from registry_svc import orm  # noqa: F401  (registers the table on Base.metadata)
from registry_svc.api import router
from registry_svc.db import Base, engine
from registry_svc.probe import run_prober

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev-convenience schema creation; production uses `alembic upgrade head`.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    stop = asyncio.Event()
    prober = asyncio.create_task(run_prober(stop))
    try:
        yield
    finally:
        stop.set()
        prober.cancel()
        try:
            await prober
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Agent Fabric — Registry", version="0.1.0", lifespan=lifespan)
setup_telemetry("registry", settings.otel_exporter_otlp_endpoint, fastapi_app=app)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "registry"}


@app.get("/.well-known/agent-card.json")
async def fabric_card() -> dict:
    """The fabric advertises itself as one composite agent."""
    return {
        "name": "agent-fabric",
        "description": "Open-source Agent Fabric control plane (registry + gateways + broker).",
        "version": "0.1.0",
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [],
    }
