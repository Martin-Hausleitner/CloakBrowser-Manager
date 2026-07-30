#!/usr/bin/env python3
"""Separate local-only MCP surface for the CloakBrowser recorder extension."""

from __future__ import annotations

import asyncio

from scripts.cbm_extension_ctl import ExtensionControlClient


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Official MCP Python SDK is missing; install mcp>=1.27,<2") from exc
    return FastMCP


def build_server(controller: ExtensionControlClient | None = None):
    FastMCP = _import_fastmcp()
    ctl = controller or ExtensionControlClient()
    server = FastMCP(
        "CloakBrowser Local Extension Control",
        instructions=(
            "Control only the local reference-only recorder extension. Never request or reveal "
            "passwords, cookies, tokens, OTP values, passkeys, proxy credentials, shell access, "
            "or arbitrary browser navigation."
        ),
        json_response=True,
    )

    async def execute(operation: str):
        return await asyncio.to_thread(ctl.execute, operation)

    @server.tool()
    async def recorder_status():
        """Return bounded recorder status without recorded values."""
        return await execute("status")

    @server.tool()
    async def recorder_start():
        """Start reference-only recording in the active extension tab."""
        return await execute("start")

    @server.tool()
    async def recorder_stop():
        """Stop recording and return the redacted export."""
        return await execute("stop")

    @server.tool()
    async def recorder_export():
        """Export the redacted recording; raw typed values are unavailable."""
        return await execute("export")

    @server.tool()
    async def recorder_clear():
        """Clear only the current in-memory extension recording."""
        return await execute("clear")

    @server.tool()
    async def recorder_compile():
        """Compile the redacted export into a Browser Use replay contract."""
        return await execute("compile")

    return server


def main() -> int:
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
