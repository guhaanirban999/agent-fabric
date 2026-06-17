"""Agent Card acquisition.

A2A agents: fetch `/.well-known/agent-card.json`, parse, and (best-effort) verify the
JWS signatures against a configured trusted JWKS.

MCP servers: don't publish A2A cards, so we connect via an MCP client, call `tools/list`,
and synthesize a card whose tools become skills — giving the broker one uniform shape
to route against.
"""

from __future__ import annotations

import json
import logging

import httpx
from google.protobuf import json_format

from a2a.types import AgentCard
from a2a.utils.signing import (
    InvalidSignaturesError,
    NoSignatureError,
    SignatureVerificationError,
    create_signature_verifier,
)
from jwt import PyJWK

from fabric_common.models import AgentSkillSummary

logger = logging.getLogger(__name__)

WELL_KNOWN_PATH = "/.well-known/agent-card.json"


# --------------------------------------------------------------------------- A2A
def _card_url(base_or_full: str) -> str:
    if base_or_full.rstrip("/").endswith("agent-card.json"):
        return base_or_full
    return base_or_full.rstrip("/") + WELL_KNOWN_PATH


async def fetch_a2a_card(card_url: str) -> dict:
    """Fetch the raw Agent Card JSON (verbatim, stored as-is in the registry)."""
    url = _card_url(card_url)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def parse_card(card_dict: dict) -> AgentCard:
    """Parse raw card JSON into the proto AgentCard.

    The SDK's card route adds back-compat fields (`url`, `preferredTransport`,
    `protocolVersion`) that aren't in the proto schema, so ignore unknown fields.
    """
    card = AgentCard()
    json_format.ParseDict(card_dict, card, ignore_unknown_fields=True)
    return card


def endpoint_from_card(card: AgentCard, card_dict: dict) -> str:
    for iface in card.supported_interfaces:
        if iface.url:
            return iface.url
    # Back-compat: top-level url emitted by the SDK serializer.
    return card_dict.get("url", "")


def skills_from_card(card: AgentCard) -> list[AgentSkillSummary]:
    out: list[AgentSkillSummary] = []
    for s in card.skills:
        out.append(
            AgentSkillSummary(
                id=s.id,
                name=s.name or s.id,
                description=s.description or "",
                tags=list(s.tags),
                examples=list(s.examples),
                input_modes=list(s.input_modes) or ["text"],
                output_modes=list(s.output_modes) or ["text"],
            )
        )
    return out


def _build_verifier(trusted_jwks_json: str):
    if not trusted_jwks_json.strip():
        return None
    try:
        jwks = json.loads(trusted_jwks_json)
        keyset = {k.get("kid"): PyJWK(k) for k in jwks.get("keys", [])}
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not load trusted JWKS: %s", exc)
        return None

    def key_provider(kid, alg):
        if kid in keyset:
            return keyset[kid]
        raise KeyError(f"no trusted key for kid={kid}")

    return create_signature_verifier(key_provider, algorithms=["RS256", "ES256"])


def verify_card_signature(card: AgentCard, trusted_jwks_json: str) -> bool:
    """Return True only if the card carries a valid signature from a trusted key."""
    verifier = _build_verifier(trusted_jwks_json)
    if verifier is None:
        return False
    try:
        verifier(card)  # raises on missing/invalid signature
        return True
    except (NoSignatureError, InvalidSignaturesError, SignatureVerificationError):
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("Signature verification error: %s", exc)
        return False


# --------------------------------------------------------------------------- MCP
async def introspect_mcp(endpoint_url: str, name_hint: str = "") -> tuple[dict, list[AgentSkillSummary]]:
    """Connect to an MCP server, list its tools, and synthesize a card + skills."""
    from fastmcp import Client

    skills: list[AgentSkillSummary] = []
    async with Client(endpoint_url) as client:
        tools = await client.list_tools()
        for t in tools:
            skills.append(
                AgentSkillSummary(
                    id=t.name,
                    name=t.name,
                    description=(t.description or "").strip(),
                    tags=["mcp", "tool"],
                    input_modes=["text"],
                    output_modes=["text"],
                    input_schema=getattr(t, "inputSchema", None) or {},
                )
            )

    synthetic_card = {
        "name": name_hint or "mcp-server",
        "description": f"MCP server exposing {len(skills)} tool(s).",
        "version": "0.0.0",
        "capabilities": {"streaming": False},
        "supportedInterfaces": [{"url": endpoint_url, "protocolBinding": "MCP"}],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [s.model_dump() for s in skills],
    }
    return synthetic_card, skills
