"""MCP client: launches the scheduling MCP server as a subprocess, performs the
MCP handshake, discovers its tools and forwards tool calls.

This is a *real* MCP integration (JSON-RPC over stdio via the official Python
SDK), not an HTTP wrapper: the agent has no hard-coded knowledge of the tool
list - names, descriptions and JSON schemas are discovered at runtime with
``tools/list`` and executed with ``tools/call``.

The agent depends only on the two-method interface below (``list_tools`` and
``call_tool``), so tests can substitute an in-process fake.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ToolResult:
    ok: bool                 # transport/protocol level success (False = MCP isError or timeout)
    data: Any                # parsed JSON object if possible, else raw text
    text: str                # raw text content


class MCPToolClient:
    def __init__(self, *, db_path: str | Path, now_iso: str, command: str | None = None,
                 args: list[str] | None = None, cwd: str | Path | None = None, timeout: float = 20.0):
        self.command = command or sys.executable
        self.args = args or ["-m", "clinic_agent.mcp_server"]
        self.cwd = str(cwd) if cwd else str(Path(__file__).resolve().parent.parent)
        # Least privilege: the server subprocess gets only what it needs (the MCP SDK adds a
        # safe default environment such as PATH/HOME). API keys are NOT passed to it.
        self.env = {"CLINIC_DB_PATH": str(db_path), "CLINIC_NOW": now_iso}
        self.timeout = timeout
        self._stack: AsyncExitStack | None = None
        self._session = None
        self.server_name = ""

    # -- lifecycle --------------------------------------------------------
    async def __aenter__(self) -> "MCPToolClient":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.command, args=self.args, env=self.env, cwd=self.cwd)
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            init = await asyncio.wait_for(self._session.initialize(), timeout=self.timeout)
            self.server_name = getattr(getattr(init, "serverInfo", None), "name", "") or "mcp-server"
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.aclose()
        self._stack, self._session = None, None

    # -- operations ---------------------------------------------------------
    async def list_tools(self) -> list[dict]:
        """Return tools in the neutral shape {name, description, input_schema}."""
        listing = await self._session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in listing.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        try:
            result = await asyncio.wait_for(self._session.call_tool(name, arguments), timeout=self.timeout)
        except asyncio.TimeoutError:
            return ToolResult(False, {"ok": False, "error_code": "TIMEOUT", "message": "Tool call timed out."}, "timeout")
        except Exception as exc:  # protocol/transport failure
            msg = f"{type(exc).__name__}: {exc}"
            return ToolResult(False, {"ok": False, "error_code": "MCP_ERROR", "message": msg}, msg)

        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = text
        return ToolResult(not getattr(result, "isError", False), data, text)
