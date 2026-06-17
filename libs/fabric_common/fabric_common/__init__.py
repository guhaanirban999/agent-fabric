"""Shared library for the Agent Fabric control plane.

Submodules:
    config          process settings (env-driven)
    models          canonical data models (AgentEntry, AuditRecord, RouteDecision, ...)
    telemetry       OpenTelemetry setup helpers
    governance      OPA client, policy-enforcement helpers, rate limiting
    auth            OIDC/JWT validation + token-exchange (on-behalf-of)
    registry_client typed client for the registry service
"""

__version__ = "0.1.0"
