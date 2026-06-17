"""Process settings, sourced from environment variables (12-factor)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infra
    database_url: str = "postgresql+asyncpg://fabric:fabric@postgres:5432/fabric"
    opa_url: str = "http://opa:8181"

    # Telemetry. Empty endpoint => tracing stays in-process (no collector required).
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "agent-fabric"

    # Fabric service discovery
    registry_url: str = "http://registry:8000"
    gateway_mcp_url: str = "http://gateway-mcp:8000"
    gateway_a2a_url: str = "http://gateway-a2a:8000"

    # Auth (Phase 2+). Empty issuer => auth disabled (dev only).
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_audience: str = "agent-fabric"
    fabric_signing_secret: str = "dev-only-change-me"

    # Broker LLM (Phase 3)
    anthropic_api_key: str = ""
    broker_model: str = "claude-opus-4-8"

    # Chat frontend (Slack bot + broker /chat)
    slack_bot_token: str = ""
    slack_app_token: str = ""
    broker_url: str = "http://broker:8000"
    chat_history_turns: int = 8

    @property
    def auth_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_jwks_url)


def get_settings() -> Settings:
    return Settings()
