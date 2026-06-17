"""A2A governance gateway.

Routes inbound A2A JSON-RPC (and SSE streams) through policy enforcement, then
forwards to the registered downstream agent. Mirrors gateway-mcp's governance so
policy and audit are identical across both protocols.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from fabric_common.auth import JWTValidator
from fabric_common.config import get_settings
from fabric_common.governance import (
    AuditSink,
    OPAClient,
    PolicyEnforcementPoint,
    TokenBucketLimiter,
)
from fabric_common.registry_client import RegistryClient
from fabric_common.telemetry import setup_telemetry
from gateway_a2a_svc.handler import A2AProxy

logging.basicConfig(level=logging.INFO)
settings = get_settings()

opa = OPAClient(settings.opa_url, fail_open=False)
limiter = TokenBucketLimiter()
audit = AuditSink(settings.database_url)
pep = PolicyEnforcementPoint(opa, limiter, audit)
validator = JWTValidator(settings.oidc_issuer, settings.oidc_jwks_url, settings.oidc_audience)
registry = RegistryClient(settings.registry_url)
proxy = A2AProxy(pep, validator, registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await audit.start()
    try:
        yield
    finally:
        await audit.aclose()
        await opa.aclose()
        await registry.aclose()


app = FastAPI(title="Agent Fabric — A2A Gateway", version="0.1.0", lifespan=lifespan)
setup_telemetry("gateway-a2a", settings.otel_exporter_otlp_endpoint, fastapi_app=app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "gateway-a2a"}


@app.get("/audit")
async def audit_recent(limit: int = 50) -> list[dict]:
    return await audit.recent(limit)


@app.post("/a2a/{agent_id}")
async def a2a_proxy(agent_id: str, request: Request):
    return await proxy.handle(agent_id, request)
