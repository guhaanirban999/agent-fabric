"""Echo MCP server fixture.

Stands in for a MuleSoft API exposed as an MCP server. Exposes a couple of trivial
tools over Streamable HTTP so the registry, MCP gateway, and broker have something
real to discover, govern, and route to.

Run: python -m echo_mcp.server   (serves on 0.0.0.0:9001 at /mcp)
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

mcp = FastMCP(name="echo-mcp")


@mcp.tool
def echo(text: str) -> str:
    """Echo the given text back to the caller."""
    return text


@mcp.tool
def reverse(text: str) -> str:
    """Return the input text reversed."""
    return text[::-1]


@mcp.tool
def add(a: float, b: float) -> float:
    """Add two numbers and return the sum."""
    return a + b


@mcp.tool
def danger(target: str) -> str:
    """A deliberately ungoverned-looking tool, NOT in the policy allow-list.
    Used to demonstrate that the gateway hides it from tools/list and blocks calls."""
    return f"danger executed against {target}"


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "9001"))
    # Streamable HTTP is the current standard remote transport; endpoint is /mcp.
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
