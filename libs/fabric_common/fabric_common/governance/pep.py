"""Policy Enforcement Point — the shared governance core for both gateways.

`enforce()` is the full path used on every real call: OPA decision -> policy-driven
rate limit -> audit row + OTel span. `check()` is the lightweight path used for
per-subject `list_tools` filtering (OPA decision only, no audit/rate-limit noise).

Building this once and sharing it across MCP and A2A is deliberate: it guarantees
policy and audit never drift between the two protocols.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fabric_common.governance.audit import AuditSink
from fabric_common.governance.opa import OPAClient
from fabric_common.governance.ratelimit import TokenBucketLimiter
from fabric_common.models import AuditRecord, PolicyDecision, PolicyInput
from fabric_common.telemetry import get_tracer
from fabric_common.telemetry.otel import current_trace_id_hex

logger = logging.getLogger(__name__)

_SECRET_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key", "credential")
_PII_HINTS = ("ssn", "email", "phone", "dob", "birth", "address")


def detect_data_classes(arg_keys: list[str]) -> list[str]:
    """Coarse data-classification from argument key names (no values inspected)."""
    classes: set[str] = set()
    for k in arg_keys:
        lk = k.lower()
        if any(h in lk for h in _SECRET_HINTS):
            classes.add("secret")
        if any(h in lk for h in _PII_HINTS):
            classes.add("pii")
    return sorted(classes)


class PolicyEnforcementPoint:
    def __init__(
        self,
        opa: OPAClient,
        limiter: TokenBucketLimiter,
        audit: AuditSink,
        tracer_name: str = "agent-fabric",
    ) -> None:
        self._opa = opa
        self._limiter = limiter
        self._audit = audit
        self._tracer = get_tracer(tracer_name)

    async def check(self, policy_input: PolicyInput) -> PolicyDecision:
        """OPA decision only — used for list filtering. No audit, no rate limiting."""
        return await self._opa.evaluate(policy_input)

    async def enforce(self, policy_input: PolicyInput) -> PolicyDecision:
        """Full enforcement: decision + rate limit + audit + span. Returns the
        (possibly allow-flipped) decision; callers gate on `decision.allow`."""
        target = _target(policy_input)
        started = time.monotonic()
        with self._tracer.start_as_current_span(f"governance.{policy_input.action}") as span:
            decision = await self._opa.evaluate(policy_input)
            allowed = decision.allow
            reason = decision.reason

            if allowed and decision.rate_limit is not None:
                key = f"{policy_input.subject.sub}:{target}"
                if not self._limiter.allow(key, decision.rate_limit, decision.rate_window_seconds):
                    allowed = False
                    reason = "rate-limited"

            latency_ms = (time.monotonic() - started) * 1000.0
            span.set_attribute("fabric.protocol", policy_input.protocol)
            span.set_attribute("fabric.action", policy_input.action)
            span.set_attribute("fabric.subject", policy_input.subject.sub)
            span.set_attribute("fabric.target", target or "")
            span.set_attribute("fabric.allowed", allowed)
            span.set_attribute("fabric.reason", reason)

            await self._audit.write(
                AuditRecord(
                    trace_id=current_trace_id_hex(),
                    timestamp=datetime.now(timezone.utc),
                    protocol=policy_input.protocol,
                    action=policy_input.action,
                    subject_sub=policy_input.subject.sub,
                    target=target,
                    domain=policy_input.domain,
                    allowed=allowed,
                    reason=reason,
                    arg_keys=policy_input.arg_keys,
                    data_classes=policy_input.data_classes,
                    latency_ms=latency_ms,
                    status="ok" if allowed else "denied",
                )
            )

            # Return the final decision (allow may have been flipped by rate limiting).
            return PolicyDecision(
                allow=allowed,
                reason=reason,
                rate_limit=decision.rate_limit,
                rate_window_seconds=decision.rate_window_seconds,
                redact_keys=decision.redact_keys,
            )


def _target(pi: PolicyInput) -> str:
    if pi.protocol == "mcp":
        return f"{pi.server or 'mcp'}:{pi.tool or '*'}"
    return f"{pi.server or 'a2a'}:{pi.skill or '*'}"
