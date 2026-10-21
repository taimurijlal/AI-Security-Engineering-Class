"""
Genuine MCP client for Module 3. GlobalTech AI talks to mcp_server.py over
the standard MCP stdio transport -- the same protocol a real MCP host uses
to talk to any MCP server. There is no shortcut here that calls Python
functions directly; every tool call in this module is a real
initialize() / list_tools() / call_tool() round trip over a subprocess
pipe, exactly as it would be against a third-party MCP server.

A fresh subprocess is started per call rather than kept alive across
Streamlit reruns. Streamlit re-executes the whole script on every
interaction, which makes a long-lived subprocess hard to manage safely
(no natural "shutdown" hook); starting small is more reliable for a
teaching app than it is fast, and tool calls here are infrequent (at most
one or two per chat turn).

TRUSTED IDENTITY INJECTION (read this before changing anything): call_tool()
always overwrites requester_id and requester_role in the outgoing
arguments with the values the caller passes in explicitly -- and every
caller in this codebase (see module.py) passes in the current session's
real app.core.security.User.employee_id / .role, never anything taken
from model-generated text. If a model's proposed tool-call JSON included
its own "requester_id" or "requester_role" keys, they are discarded here,
unconditionally, before the call is sent. The LLM never decides who the
user is -- this function, driven by the trusted session, does.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

SERVER_SCRIPT = str(Path(__file__).with_name("mcp_server.py"))
# mcp_server.py does `from app.modules.module3_mcp import backend, tool_policy`,
# which only resolves if the top-level `app` package (i.e. the project
# root) is on the subprocess's sys.path. Running `python mcp_server.py`
# directly only puts that file's own directory on sys.path[0], not the
# project root -- so the project root is injected via PYTHONPATH instead.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ToolCallRecord:
    """Evidence of one real MCP tool call, for the MCP Inspector panel and
    for findings export -- the Module 3 analogue of Module 2's
    RetrievedChunk. Carries both what was requested and what the server
    says actually happened, so the Inspector can show "expected DENY,
    observed EXECUTED" as a control-failure finding independent of
    whatever the model said in its final chat response."""

    tool_name: str
    arguments_requested: dict[str, Any]
    arguments_sent: dict[str, Any]
    executed: bool
    expected_authorization: bool | None
    policy_reason: str | None
    result: Any
    is_error: bool
    raw_text: str = field(default="", repr=False)


def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    project_root_str = str(PROJECT_ROOT)
    env["PYTHONPATH"] = (
        f"{project_root_str}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else project_root_str
    )
    return StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT], env=env)


async def _list_tools_async() -> list[dict[str, Any]]:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in result.tools
            ]


def list_tools() -> list[dict[str, Any]]:
    """Real MCP tool discovery: starts the server, calls list_tools() over
    stdio, and returns what the server itself reports -- not a hardcoded
    Python list the host maintains separately."""
    return asyncio.run(_list_tools_async())


async def _call_tool_async(tool_name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, bool, str]:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            raw_text = ""
            for block in result.content or []:
                if isinstance(block, TextContent):
                    raw_text = block.text
                    break
            parsed: dict[str, Any] | None = None
            if raw_text:
                try:
                    parsed = json.loads(raw_text)
                except json.JSONDecodeError:
                    parsed = None
            return parsed, bool(result.isError), raw_text


def call_tool(
    tool_name: str,
    requester_id: str,
    requester_role: str,
    arguments_requested: dict[str, Any],
) -> ToolCallRecord:
    """Execute one real MCP tool call over stdio for the given tool_name.

    requester_id / requester_role must be the trusted session identity
    (app.core.security.User.employee_id / .role) -- not anything read from
    model output. Any "requester_id"/"requester_role" keys present in
    arguments_requested (e.g. because a model's proposed JSON included
    them) are discarded and replaced unconditionally.
    """
    arguments_sent = {
        key: value
        for key, value in arguments_requested.items()
        if key not in ("requester_id", "requester_role")
    }
    arguments_sent["requester_id"] = requester_id
    arguments_sent["requester_role"] = requester_role

    try:
        parsed, is_error, raw_text = asyncio.run(_call_tool_async(tool_name, arguments_sent))
    except Exception as exc:  # defensive: a broken subprocess must not crash the UI
        return ToolCallRecord(
            tool_name=tool_name,
            arguments_requested=arguments_requested,
            arguments_sent=arguments_sent,
            executed=False,
            expected_authorization=None,
            policy_reason=f"MCP call failed before the server could respond: {exc}",
            result=None,
            is_error=True,
            raw_text="",
        )

    if parsed is None:
        return ToolCallRecord(
            tool_name=tool_name,
            arguments_requested=arguments_requested,
            arguments_sent=arguments_sent,
            executed=False,
            expected_authorization=None,
            policy_reason="Tool returned a non-JSON or empty result.",
            result=None,
            is_error=is_error,
            raw_text=raw_text,
        )

    return ToolCallRecord(
        tool_name=tool_name,
        arguments_requested=arguments_requested,
        arguments_sent=arguments_sent,
        executed=bool(parsed.get("executed", not is_error)),
        expected_authorization=parsed.get("expected_authorization"),
        policy_reason=parsed.get("policy_reason"),
        result=parsed.get("result"),
        is_error=is_error,
        raw_text=raw_text,
    )
