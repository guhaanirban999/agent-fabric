"""OIDC/JWT validation -> Subject.

In dev (no issuer configured) every caller is the `anonymous` subject so the stack
runs without an IdP. When `OIDC_ISSUER`/`OIDC_JWKS_URL` are set, bearer tokens are
validated against the issuer's JWKS. Token-exchange / on-behalf-of minting lands in
Phase 4 (`mint_downstream_token`).
"""

from __future__ import annotations

import logging

import jwt
from jwt import PyJWKClient

from fabric_common.models import Subject

logger = logging.getLogger(__name__)


class JWTValidator:
    def __init__(
        self,
        issuer: str = "",
        jwks_url: str = "",
        audience: str = "agent-fabric",
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self._enabled = bool(issuer and jwks_url)
        self._jwks: PyJWKClient | None = PyJWKClient(jwks_url) if self._enabled else None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def validate(self, token: str | None) -> Subject:
        if not self._enabled:
            return Subject(sub="anonymous", scopes=["*"])
        if not token:
            raise PermissionError("missing bearer token")
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)  # type: ignore[union-attr]
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self.audience,
                issuer=self.issuer,
            )
        except Exception as exc:
            logger.info("JWT validation failed: %s", exc)
            raise PermissionError("invalid token") from exc

        scope = claims.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else list(scope)
        return Subject(
            sub=claims.get("sub", "unknown"),
            scopes=scopes,
            act=(claims.get("act") or {}).get("sub") if isinstance(claims.get("act"), dict) else None,
            claims=claims,
        )


def subject_from_request(validator: JWTValidator, authorization_header: str | None) -> Subject:
    """Extract a bearer token from an Authorization header and validate it."""
    token = None
    if authorization_header and authorization_header.lower().startswith("bearer "):
        token = authorization_header[7:].strip()
    return validator.validate(token)
