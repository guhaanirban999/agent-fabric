"""Governance models: the policy decision contract and the audit record.

These are shared by both gateways so policy and audit never drift between MCP and A2A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Subject(BaseModel):
    """The authenticated caller, derived from a validated JWT (or anonymous in dev)."""

    sub: str = "anonymous"
    scopes: list[str] = Field(default_factory=list)
    # `act` (actor) supports on-behalf-of chains: fabric acting for an end user.
    act: str | None = None
    claims: dict[str, Any] = Field(default_factory=dict)


class PolicyInput(BaseModel):
    """Sent to OPA as `input`. One shape for both protocols."""

    subject: Subject
    protocol: str                     # "mcp" | "a2a"
    action: str                       # e.g. "mcp.call_tool", "a2a.message_send"
    # Target of the action
    server: str | None = None         # MCP server / A2A agent id or name
    tool: str | None = None           # MCP tool name (None for A2A)
    skill: str | None = None          # A2A skill id (None for MCP)
    domain: str | None = None
    # Lightweight argument metadata — keys + detected data classes, not raw values.
    arg_keys: list[str] = Field(default_factory=list)
    data_classes: list[str] = Field(default_factory=list)  # e.g. ["pii", "secret"]


class PolicyDecision(BaseModel):
    """OPA decision returned to the enforcement point."""

    allow: bool = False
    reason: str = ""
    # Policy-driven rate budget (requests per window). None => unlimited.
    rate_limit: int | None = None
    rate_window_seconds: int = 60
    # Which arg keys must be redacted from audit/logs.
    redact_keys: list[str] = Field(default_factory=list)


class AuditRecord(BaseModel):
    """One row per governed interaction. Persisted to Postgres and emitted as an OTel span."""

    id: UUID = Field(default_factory=uuid4)
    trace_id: str | None = None       # ties registry->broker->gateway->downstream
    timestamp: datetime | None = None

    protocol: str
    action: str
    subject_sub: str
    target: str | None = None         # "server:tool" or "agent:skill"
    domain: str | None = None

    allowed: bool = False
    reason: str = ""
    # Redacted argument metadata only — never raw payloads by default.
    arg_keys: list[str] = Field(default_factory=list)
    data_classes: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    status: str = "ok"                # ok | denied | error
    error: str | None = None
