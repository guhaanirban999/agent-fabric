"""Unit tests for the token-exchange / OBO helper (no network)."""

from __future__ import annotations

import jwt
import pytest

from fabric_common.auth import mint_downstream_token, verify_internal_token

SECRET = "test-secret"


def test_obo_token_roundtrip_carries_principal_and_actor():
    token = mint_downstream_token(
        SECRET, principal_sub="alice@corp", audience="echo-mcp", scopes=["tools:call"]
    )
    claims = verify_internal_token(SECRET, token, audience="echo-mcp")
    assert claims["sub"] == "alice@corp"          # original principal preserved
    assert claims["act"]["sub"] == "agent-fabric"  # fabric is the actor (on-behalf-of)
    assert claims["iss"] == "agent-fabric"
    assert "tools:call" in claims["scope"]


def test_obo_token_rejects_wrong_audience():
    token = mint_downstream_token(SECRET, principal_sub="bob", audience="echo-mcp")
    with pytest.raises(jwt.InvalidAudienceError):
        verify_internal_token(SECRET, token, audience="some-other-service")


def test_obo_token_rejects_tampered_secret():
    token = mint_downstream_token(SECRET, principal_sub="bob", audience="echo-mcp")
    with pytest.raises(jwt.InvalidSignatureError):
        verify_internal_token("wrong-secret", token, audience="echo-mcp")
