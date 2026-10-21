"""
Deterministic, LLM-independent authorization for Module 3's MCP tools.

This is the MCP-security analogue of app/modules/module2_rag/rag.py's
is_authorized(): a pure function of (tool, requester identity, target of
the call) that computes the *correct* allow/deny decision, with no
knowledge of what any model said and no ability to be talked out of its
answer. The decision is deterministic and external to the model -- the
only kind of boundary this course counts as a real security control.

requester_id and requester_role here must always originate from the
trusted app.core.security.User object for the current session, injected
by mcp_client.call_tool() -- never from anything a model wrote into a
tool-call's JSON arguments. This function has no way to verify that on its
own; it trusts its caller the same way rag.py's is_authorized() trusts
whatever user_role it's handed. Keeping that trust boundary intact is the
host's (mcp_client.py's) job, not this module's.

DELIBERATE STARTER VULNERABILITY NOTE: this function itself is correct in
both the vulnerable and the remediated state of the lab -- exactly like
is_authorized() in Module 2. The bug students find in Module 3 is not
here. It is in mcp_server.py, where every tool calls authorize(), gets
back the right answer, and then ignores it and executes anyway. See that
file's docstring.
"""

from __future__ import annotations

from dataclasses import dataclass

GET_EMPLOYEE_PROFILE = "get_employee_profile"
GET_SALARY_INFO = "get_salary_info"
LIST_SUPPORT_TICKETS = "list_support_tickets"
CREATE_SUPPORT_TICKET = "create_support_ticket"
# Additive for Module 4 (Agent Security): a second write action so the
# agent lab has a real state-changing tool to route through
# propose -> authorize -> risk -> approval -> execute. Does not change any
# of the four tools/rules above.
UPDATE_TICKET_STATUS = "update_ticket_status"

ALL_TOOLS = (
    GET_EMPLOYEE_PROFILE,
    GET_SALARY_INFO,
    LIST_SUPPORT_TICKETS,
    CREATE_SUPPORT_TICKET,
    UPDATE_TICKET_STATUS,
)
WRITE_TOOLS = {CREATE_SUPPORT_TICKET, UPDATE_TICKET_STATUS}

KNOWN_ROLES = {"employee", "hr", "admin"}
PRIVILEGED_ROLES = {"hr", "admin"}

VALID_TICKET_STATUSES = {"open", "in_progress", "resolved", "closed"}


def _valid_status_transition(current_status: str | None, new_status: str, requester_role: str) -> bool:
    """Only a meaningful, forward-ish transition is allowed; reopening a
    closed ticket is an elevated action (hr/admin only) because it changes
    a record a standard employee already considers settled."""
    if current_status is None:
        return True  # unknown prior state: let the status-validity check alone decide
    if new_status == current_status:
        return False  # no-op transitions are not a valid "update"
    if current_status == "closed" and requester_role not in PRIVILEGED_ROLES:
        return False
    return True


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str


def _identity_problem(requester_id: str | None, requester_role: str | None) -> AuthorizationDecision | None:
    """Catches the two "identity itself is untrustworthy" cases every tool
    must handle the same way, before any tool-specific rule runs: a blank/
    missing identity, or a role string that isn't one of the three this
    system knows about at all."""
    if not requester_id or not requester_role:
        return AuthorizationDecision(False, "Missing or malformed requester identity.")
    if requester_role not in KNOWN_ROLES:
        return AuthorizationDecision(False, f"Unknown role '{requester_role}'.")
    return None


def authorize(tool_name: str, requester_id: str, requester_role: str, **kwargs: object) -> AuthorizationDecision:
    """Decide whether (requester_id, requester_role) may invoke tool_name
    with the given kwargs (e.g. employee_id=... for the record being
    targeted). Returns an AuthorizationDecision; never raises for a normal
    deny, only for a tool_name this policy has never heard of."""
    identity_problem = _identity_problem(requester_id, requester_role)
    if identity_problem is not None:
        return identity_problem

    if tool_name == GET_EMPLOYEE_PROFILE:
        target_id = kwargs.get("employee_id")
        if requester_role in PRIVILEGED_ROLES:
            return AuthorizationDecision(True, f"Role '{requester_role}' may view any employee profile.")
        if target_id == requester_id:
            return AuthorizationDecision(True, "Employees may view their own profile.")
        return AuthorizationDecision(False, "Employees may only view their own profile.")

    if tool_name == GET_SALARY_INFO:
        if requester_role in PRIVILEGED_ROLES:
            return AuthorizationDecision(True, f"Role '{requester_role}' may view salary records.")
        return AuthorizationDecision(False, "Salary information is restricted to HR and Admin roles.")

    if tool_name == LIST_SUPPORT_TICKETS:
        target_id = kwargs.get("employee_id") or requester_id
        if requester_role in PRIVILEGED_ROLES:
            return AuthorizationDecision(True, f"Role '{requester_role}' may view any employee's tickets.")
        if target_id == requester_id:
            return AuthorizationDecision(True, "Employees may view their own tickets.")
        return AuthorizationDecision(False, "Employees may only view their own tickets.")

    if tool_name == CREATE_SUPPORT_TICKET:
        target_id = kwargs.get("employee_id") or requester_id
        if requester_role in PRIVILEGED_ROLES:
            return AuthorizationDecision(
                True, f"Role '{requester_role}' may file a ticket on behalf of any employee."
            )
        if target_id == requester_id:
            return AuthorizationDecision(True, "Employees may file tickets for themselves.")
        return AuthorizationDecision(False, "Employees may only file tickets for themselves.")

    if tool_name == UPDATE_TICKET_STATUS:
        # target_id here is the ticket's *owning* employee_id, looked up by
        # the caller (mcp_server.py) before calling authorize() -- this
        # function stays backend-free, exactly like every rule above.
        target_id = kwargs.get("employee_id")
        new_status = str(kwargs.get("new_status") or "")
        current_status_raw = kwargs.get("current_status")
        current_status = str(current_status_raw) if current_status_raw is not None else None

        if new_status not in VALID_TICKET_STATUSES:
            return AuthorizationDecision(False, f"'{new_status}' is not a recognized ticket status.")
        if not _valid_status_transition(current_status, new_status, requester_role):
            return AuthorizationDecision(
                False,
                f"Transition from '{current_status}' to '{new_status}' is not permitted for role "
                f"'{requester_role}'.",
            )
        if requester_role in PRIVILEGED_ROLES:
            return AuthorizationDecision(True, f"Role '{requester_role}' may update any employee's ticket.")
        if target_id == requester_id:
            return AuthorizationDecision(True, "Employees may update the status of their own tickets.")
        return AuthorizationDecision(False, "Employees may only update the status of their own tickets.")

    raise ValueError(f"tool_policy.authorize() has no rule for unknown tool '{tool_name}'.")
