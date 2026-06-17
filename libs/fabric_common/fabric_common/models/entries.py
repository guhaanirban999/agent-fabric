"""Canonical registry models.

Every registered thing — an A2A agent, an MCP tool server, or a remote A2A agent —
is described by a single canonical descriptor built around the A2A **Agent Card**.
MCP servers don't publish A2A cards, so the registry synthesizes one whose tools
become `skills` (see `services/registry`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl


class AgentKind(str, Enum):
    A2A_AGENT = "a2a_agent"        # native A2A agent in our control
    A2A_REMOTE = "a2a_remote"      # external/3rd-party A2A agent
    MCP_SERVER = "mcp_server"      # MCP tool server (e.g. a MuleSoft API exposed via MCP)


class Transport(str, Enum):
    JSONRPC = "jsonrpc"            # A2A JSON-RPC 2.0
    REST = "rest"                  # A2A HTTP+JSON
    GRPC = "grpc"                  # A2A gRPC
    STREAMABLE_HTTP = "streamable-http"  # MCP streamable HTTP
    STDIO = "stdio"               # MCP stdio (local)


class HealthStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class AuthScheme(BaseModel):
    """How the fabric authenticates to this downstream entry."""

    type: str = "none"            # none | oauth2_client_credentials | api_key | mtls
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    # Secret material is referenced, never stored inline in the registry row.
    secret_ref: str | None = None


class AgentSkillSummary(BaseModel):
    """Flattened skill the broker routes against. Mirrors A2A `AgentSkill`;
    for MCP servers each tool maps to one of these."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])
    # JSON Schema of the tool/skill arguments (MCP tools have this). Fed to the LLM
    # router so it produces exactly the right argument names/types.
    input_schema: dict = Field(default_factory=dict)


class AgentEntry(BaseModel):
    """A registry row. `agent_card` holds the full A2A AgentCard JSON verbatim."""

    id: UUID = Field(default_factory=uuid4)
    kind: AgentKind
    name: str
    version: str = "0.0.0"
    description: str = ""

    domain: str = "default"               # broker grouping unit
    tags: list[str] = Field(default_factory=list)

    endpoint_url: HttpUrl                  # base URL of the agent / MCP server
    transport: Transport
    auth: AuthScheme = Field(default_factory=AuthScheme)

    # Full A2A AgentCard (verbatim for A2A; synthesized for MCP). Stored as JSONB.
    agent_card: dict[str, Any] = Field(default_factory=dict)
    skills: list[AgentSkillSummary] = Field(default_factory=list)

    # Trust: v1.0 A2A cards can be JWS-signed; we verify on registration.
    card_jws: str | None = None
    trusted: bool = False

    # Liveness
    health: HealthStatus = HealthStatus.UNKNOWN
    last_heartbeat: datetime | None = None

    registered_by: str = "system"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RegisterRequest(BaseModel):
    """Self-registration payload.

    For A2A: provide `card_url`; the registry fetches `/.well-known/agent-card.json`.
    For MCP: provide `endpoint_url` + `transport`; the registry calls `tools/list`
    through the MCP gateway and synthesizes a card.
    """

    kind: AgentKind
    name: str | None = None          # required for MCP (no card to name it); ignored for A2A
    domain: str = "default"
    tags: list[str] = Field(default_factory=list)
    card_url: str | None = None
    endpoint_url: str | None = None
    transport: Transport | None = None
    auth: AuthScheme = Field(default_factory=AuthScheme)
    registered_by: str = "system"
