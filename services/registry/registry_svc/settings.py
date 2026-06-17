"""Registry-specific settings, extending the shared fabric settings."""

from __future__ import annotations

from fabric_common.config import Settings as BaseSettings


class RegistrySettings(BaseSettings):
    # Reject registration of A2A cards that fail JWS verification.
    # Dev default False (echo fixture is unsigned); set True in production.
    registry_require_signed: bool = False
    # Trusted JWKS for verifying signed Agent Cards (inline JSON string).
    registry_trusted_jwks: str = ""

    # Health prober
    registry_heartbeat_interval: int = 30   # seconds between probes
    registry_heartbeat_timeout: int = 90    # mark DOWN if no success within this window


def get_registry_settings() -> RegistrySettings:
    return RegistrySettings()
