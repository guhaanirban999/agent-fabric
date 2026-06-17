"""Build and maintain the composite MCP server.

Proxies every registered MCP backend and mounts them behind one governed
Streamable-HTTP endpoint. Backends are loaded from the registry at startup and can be
refreshed at runtime via `MountManager.reload()` (POST /admin/reload) — newly
registered MCP servers are mounted live. Removing a backend still needs a restart
(FastMCP unmount is not relied upon here).
"""

from __future__ import annotations

import logging
import time

import httpx
from fastmcp import FastMCP
from fastmcp.server import create_proxy

from gateway_mcp_svc.governance import GovernanceMiddleware

logger = logging.getLogger(__name__)


def fetch_mcp_backends(registry_url: str, retries: int = 10, delay: float = 2.0) -> list[dict]:
    url = f"{registry_url.rstrip('/')}/agents"
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, params={"kind": "mcp_server"})
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.info("registry not ready (%s/%s): %s", attempt + 1, retries, exc)
            time.sleep(delay)
    logger.warning("could not load MCP backends from registry; starting empty")
    return []


class MountManager:
    """Owns the composite server and the set of mounted backend endpoints."""

    def __init__(self, composite: FastMCP, registry_url: str) -> None:
        self.composite = composite
        self._registry_url = registry_url
        self._mounted: set[str] = set()

    def _mount(self, backend: dict) -> bool:
        endpoint = backend["endpoint_url"]
        if endpoint in self._mounted:
            return False
        try:
            self.composite.mount(create_proxy(endpoint))
            self._mounted.add(endpoint)
            logger.info("mounted MCP backend: %s (%s)", backend.get("name"), endpoint)
            return True
        except Exception as exc:
            logger.warning("failed to mount %s: %s", endpoint, exc)
            return False

    def reload(self) -> dict:
        """Mount any registered MCP backends not already mounted. Returns a summary."""
        backends = fetch_mcp_backends(self._registry_url, retries=1)
        added = [b["endpoint_url"] for b in backends if self._mount(b)]
        return {"mounted_total": len(self._mounted), "newly_mounted": added}

    @property
    def mounted(self) -> list[str]:
        return sorted(self._mounted)


def build_composite(pep, validator, registry_url: str) -> tuple[FastMCP, MountManager]:
    composite = FastMCP(name="fabric-mcp-gateway")
    manager = MountManager(composite, registry_url)
    for backend in fetch_mcp_backends(registry_url):
        manager._mount(backend)
    composite.add_middleware(GovernanceMiddleware(pep, validator))
    return composite, manager
