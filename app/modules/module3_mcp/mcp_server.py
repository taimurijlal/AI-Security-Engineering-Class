"""
Module 3's real MCP server: GlobalTech's fictional HR/IT assistant backend,
exposed over the standard Model Context Protocol using the stdio
transport. mcp_client.py launches this file as a genuine subprocess and
speaks MCP to it -- this is not a Python function dressed up and called an
"MCP tool"; it is a separate process, started over stdio exactly the way a
real MCP host launches a real MCP server, discoverable via list_tools()
and invoked via call_tool() like any other MCP server would be.

DELIBERATE STARTER VULNERABILITY (read this before "fixing" anything):
every tool below calls tool_policy.authorize() and gets back the *correct*
decision -- that part of the system works. Each tool even returns that
decision to the caller as expected_authorization / policy_reason, so the
MCP Inspector in the UI can show you what *should* have happened. But look
closely: none of these functions actually stops and returns early when
decision.allowed is False. The backend read (or, for create_support_ticket,
the write) happens unconditionally, before and regardless of the decision.
This is the exact same bug family as Module 1's context.py (hands
RESTRICTED_CONTEXT to every role) and Module 2's retrieve_chunks() (ranks
every chunk for every role): a correct check that is computed, surfaced for
observability, and never actually wired to the thing that matters. Fixing
Module 3 means adding, in each tool, something like:

    if not decision.allowed:
        return {"executed": False, "expected_authorization": False,
                "policy_reason": decision.reason, ...}

*before* the backend call -- not after, and not instead of computing it.

TRUSTED IDENTITY: requester_id and requester_role are ordinary string
parameters from this server's point of view; a server has no built-in way
to know whether they are trustworthy. That trust boundary is the host's
job: mcp_client.py always overwrites these two arguments with values taken
from the current session's real, authenticated app.core.security.User
before the call is sent over the wire, discarding anything a model tried
to put there. The model never decides who the user is -- the application
does, and only the application's decision ever reaches this process.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.modules.module3_mcp import backend, tool_policy

mcp = FastMCP("GlobalTechHRIT")


@mcp.tool()
def get_employee_profile(requester_id: str, requester_role: str, employee_id: str) -> dict:
    """Look up an employee's directory profile (name, title, department, manager_id) by employee_id."""
    decision = tool_policy.authorize(
        tool_policy.GET_EMPLOYEE_PROFILE, requester_id, requester_role, employee_id=employee_id
    )
    record = backend.find_employee(employee_id)
    return {
        "executed": True,
        "expected_authorization": decision.allowed,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": record if record is not None else {"error": f"No employee found with id '{employee_id}'."},
    }


@mcp.tool()
def get_salary_info(requester_id: str, requester_role: str, employee_id: str) -> dict:
    """Look up an employee's compensation record (base salary, bonus target) by employee_id. Restricted to HR and Admin."""
    decision = tool_policy.authorize(
        tool_policy.GET_SALARY_INFO, requester_id, requester_role, employee_id=employee_id
    )
    record = backend.find_salary(employee_id)
    return {
        "executed": True,
        "expected_authorization": decision.allowed,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": record if record is not None else {"error": f"No salary record found for '{employee_id}'."},
    }


@mcp.tool()
def list_support_tickets(requester_id: str, requester_role: str, employee_id: str | None = None) -> dict:
    """List IT/HR support tickets filed by an employee_id (defaults to the caller's own tickets if omitted)."""
    target_id = employee_id or requester_id
    decision = tool_policy.authorize(
        tool_policy.LIST_SUPPORT_TICKETS, requester_id, requester_role, employee_id=target_id
    )
    results = backend.find_tickets_for(target_id)
    return {
        "executed": True,
        "expected_authorization": decision.allowed,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": results,
    }


@mcp.tool()
def create_support_ticket(
    requester_id: str,
    requester_role: str,
    subject: str,
    description: str,
    employee_id: str | None = None,
) -> dict:
    """File a new IT/HR support ticket. Defaults to filing on behalf of the caller. This is a WRITE action."""
    target_id = employee_id or requester_id
    decision = tool_policy.authorize(
        tool_policy.CREATE_SUPPORT_TICKET, requester_id, requester_role, employee_id=target_id
    )
    ticket = backend.create_ticket(target_id, subject, description)
    return {
        "executed": True,
        "expected_authorization": decision.allowed,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": ticket,
    }


@mcp.tool()
def update_ticket_status(
    requester_id: str,
    requester_role: str,
    ticket_id: str,
    new_status: str,
    resolution_note: str = "",
) -> dict:
    """Update a support ticket's status and attach a resolution note.
    Restricted to the ticket's owning employee, or HR/Admin. This is a
    WRITE action.

    ADDITIVE FOR MODULE 4 (Agent Security): unlike the four tools above,
    this tool is NOT part of Module 3's "find the bug" exercise -- it ships
    already gated on decision.allowed, because Module 4's agent lab needs a
    second real state-changing tool to route through its own
    propose -> authorize -> risk -> approval -> execute pipeline, and the
    bug being taught in Module 4 lives at the agent layer (missing
    approval, goal-scope expansion, etc.), not here. Fixing this tool is
    not a Module 3 lab step.
    """
    existing = backend.find_ticket(ticket_id)
    if existing is None:
        return {
            "executed": False,
            "expected_authorization": None,
            "policy_reason": f"No ticket found with id '{ticket_id}'.",
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": {"error": f"No ticket found with id '{ticket_id}'."},
        }

    decision = tool_policy.authorize(
        tool_policy.UPDATE_TICKET_STATUS,
        requester_id,
        requester_role,
        employee_id=existing["employee_id"],
        ticket_id=ticket_id,
        new_status=new_status,
        current_status=existing["status"],
    )

    if not decision.allowed:
        return {
            "executed": False,
            "expected_authorization": False,
            "policy_reason": decision.reason,
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": {"error": "Not authorized to update this ticket."},
        }

    updated = backend.update_ticket(ticket_id, new_status, resolution_note)
    return {
        "executed": True,
        "expected_authorization": True,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": updated,
    }


if __name__ == "__main__":
    mcp.run()
