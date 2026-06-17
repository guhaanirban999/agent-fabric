"""Token-exchange / on-behalf-of (OBO) for downstream auth propagation.

The fabric is a middleman: a gateway validates the *caller's* token, then mints a
short-lived internal token that carries both the original principal (`sub`) and the
fabric as actor (`act`). Downstream agents/MCP servers see who the real principal is
without the gateway blindly forwarding an inbound token (whose audience wouldn't match).

This is the dev/internal implementation (HS256 with FABRIC_SIGNING_SECRET). In
production, swap for RFC 8693 token exchange against your IdP, or sign with an RS256
fabric key whose JWKS downstreams trust. Wiring: with auth enabled, a gateway calls
`mint_downstream_token(...)` and attaches `Authorization: Bearer <token>` to the
forwarded request (see gateway handler comments).
"""

from __future__ import annotations

import time

import jwt

ISSUER = "agent-fabric"
ALGORITHM = "HS256"


def mint_downstream_token(
    secret: str,
    principal_sub: str,
    audience: str,
    *,
    actor: str = ISSUER,
    scopes: list[str] | None = None,
    ttl_seconds: int = 300,
) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": principal_sub,             # the original principal
        "act": {"sub": actor},            # the actor acting on its behalf (the fabric)
        "aud": audience,                  # the downstream service
        "scope": " ".join(scopes or []),
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_internal_token(secret: str, token: str, audience: str) -> dict:
    """Validate a fabric-minted OBO token. Raises jwt exceptions on failure."""
    return jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        audience=audience,
        issuer=ISSUER,
    )
