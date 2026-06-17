"""MCP governance gateway.

Exposes one governed Streamable-HTTP endpoint at `/mcp` that proxies+aggregates all
registered MCP servers, enforcing OPA policy + rate limiting + audit + tracing on
every `tools/call` (and filtering `tools/list` per subject).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fabric_common.auth import JWTValidator
from fabric_common.governance import (
    AuditSink,
    OPAClient,
    PolicyEnforcementPoint,
    TokenBucketLimiter,
)
from fabric_common.config import get_settings
from fabric_common.telemetry import setup_telemetry
from gateway_mcp_svc.proxy import build_composite

logging.basicConfig(level=logging.INFO)
settings = get_settings()

# Governance wiring (shared core).
opa = OPAClient(settings.opa_url, fail_open=False)
limiter = TokenBucketLimiter()
audit = AuditSink(settings.database_url)
pep = PolicyEnforcementPoint(opa, limiter, audit)
validator = JWTValidator(settings.oidc_issuer, settings.oidc_jwks_url, settings.oidc_audience)

# Build the composite MCP server (loads backends from the registry at startup).
composite, mounts = build_composite(pep, validator, settings.registry_url)
mcp_app = composite.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await audit.start()
    # Nest the FastMCP session-manager lifespan so /mcp works.
    async with mcp_app.lifespan(app):
        yield
    await audit.aclose()
    await opa.aclose()


app = FastAPI(title="Agent Fabric — MCP Gateway", version="0.1.0", lifespan=lifespan)
setup_telemetry("gateway-mcp", settings.otel_exporter_otlp_endpoint, fastapi_app=app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "gateway-mcp"}


@app.get("/admin/backends")
async def admin_backends() -> dict:
    return {"mounted": mounts.mounted}


@app.post("/admin/reload")
async def admin_reload() -> dict:
    """Mount any newly-registered MCP servers without a restart."""
    return mounts.reload()


@app.get("/audit")
async def audit_recent(limit: int = 50) -> list[dict]:
    return await audit.recent(limit)


# Mount the governed MCP endpoint last so the routes above take precedence.
app.mount("/", mcp_app)
