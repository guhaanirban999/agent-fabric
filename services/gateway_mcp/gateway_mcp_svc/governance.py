"""FastMCP middleware that enforces fabric governance on every MCP interaction.

`on_call_tool` -> full PEP enforce (OPA + rate limit + audit + span); deny raises
ToolError. `on_list_tools` -> per-subject filtering so an agent only sees the tools
its policy permits (governance doubling as discovery scoping).
"""

from __future__ import annotations

import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

from fabric_common.auth import JWTValidator, subject_from_request
from fabric_common.governance import PolicyEnforcementPoint, detect_data_classes
from fabric_common.models import PolicyInput, Subject

logger = logging.getLogger(__name__)


class GovernanceMiddleware(Middleware):
    def __init__(self, pep: PolicyEnforcementPoint, validator: JWTValidator) -> None:
        self._pep = pep
        self._validator = validator

    def _subject(self) -> Subject:
        try:
            headers = get_http_headers() or {}
            return subject_from_request(self._validator, headers.get("authorization"))
        except PermissionError:
            # Unauthenticated when auth is enabled -> minimal subject (policy will deny).
            return Subject(sub="unauthenticated", scopes=[])

    async def on_call_tool(self, context, call_next):
        params = context.message
        tool = getattr(params, "name", "")
        args = getattr(params, "arguments", None) or {}
        arg_keys = list(args.keys())
        decision = await self._pep.enforce(
            PolicyInput(
                subject=self._subject(),
                protocol="mcp",
                action="mcp.call_tool",
                server="gateway-mcp",
                tool=tool,
                arg_keys=arg_keys,
                data_classes=detect_data_classes(arg_keys),
            )
        )
        if not decision.allow:
            raise ToolError(f"policy denied: {decision.reason}")
        return await call_next(context)

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        subject = self._subject()
        visible = []
        for tool in tools:
            decision = await self._pep.check(
                PolicyInput(
                    subject=subject,
                    protocol="mcp",
                    action="mcp.call_tool",
                    server="gateway-mcp",
                    tool=tool.name,
                )
            )
            if decision.allow:
                visible.append(tool)
        return visible
