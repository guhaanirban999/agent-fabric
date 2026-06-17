"""Open Policy Agent client — the single Policy Decision Point for both gateways.

The gateways are enforcement points (PEPs); OPA is the decision point (PDP), queried
locally over HTTP. Decisions are **fail-closed** by default: if OPA is unreachable,
deny. Set `fail_open=True` only for non-production.
"""

from __future__ import annotations

import logging

import httpx

from fabric_common.models import PolicyDecision, PolicyInput

logger = logging.getLogger(__name__)


class OPAClient:
    def __init__(
        self,
        opa_url: str,
        decision_path: str = "fabric/authz",
        *,
        fail_open: bool = False,
        timeout: float = 2.0,
    ) -> None:
        # OPA data API: POST /v1/data/<package path>
        self._url = f"{opa_url.rstrip('/')}/v1/data/{decision_path.strip('/')}"
        self._fail_open = fail_open
        self._client = httpx.AsyncClient(timeout=timeout)

    async def evaluate(self, policy_input: PolicyInput) -> PolicyDecision:
        try:
            resp = await self._client.post(
                self._url, json={"input": policy_input.model_dump(mode="json")}
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
        except Exception as exc:
            logger.warning("OPA evaluation failed (%s); fail_open=%s", exc, self._fail_open)
            if self._fail_open:
                return PolicyDecision(allow=True, reason="opa-unreachable-fail-open")
            return PolicyDecision(allow=False, reason="opa-unreachable-fail-closed")

        # An empty/undefined result means no rule matched -> deny.
        if not result:
            return PolicyDecision(allow=False, reason="no-policy-match")
        return PolicyDecision.model_validate(result)

    async def aclose(self) -> None:
        await self._client.aclose()
