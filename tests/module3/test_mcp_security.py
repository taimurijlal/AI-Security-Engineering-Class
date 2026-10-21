"""
Deterministic MCP security tests for GlobalTech AI Module 3.

These tests check the authorization boundary in
app/modules/module3_mcp/tool_policy.py and the four real MCP tools in
app/modules/module3_mcp/mcp_server.py directly. They never assert on LLM
wording and never call an LLM provider -- consistent with Module 1 and
Module 2's testing philosophy: test whether authorization was *evaluated
and enforced*, not whether a model happens to refuse a request on a given
run.

Do NOT "fix" a failing test here by rewriting it to check the model's
response, a tool's docstring, or a system-prompt rule instead of what the
server actually executed. The whole point of this lab is that the
authorization decision must be enforceable without depending on the model
at all -- tool_policy.authorize() already computes the right answer; the
starter mcp_server.py just doesn't act on it.
"""

import shutil

import pytest

from app.modules.module3_mcp import backend
from app.modules.module3_mcp import mcp_server as srv
from app.modules.module3_mcp import tool_policy
from app.modules.module3_mcp.tool_policy import authorize

ALICE = ("E1001", "employee")  # Engineering employee
BOB = ("E1002", "employee")  # Sales employee
HELEN = ("E1003", "hr")  # HR
ADMIN = ("E1004", "admin")  # IT admin


@pytest.fixture()
def isolated_tickets_file(tmp_path, monkeypatch):
    """create_support_ticket() is a real write action against
    backend.TICKETS_FILE. Tests that exercise it must not mutate the
    shared data/mcp/tickets.json fixture every time the suite runs --
    point the backend at a throwaway copy for the duration of the test."""
    tmp_file = tmp_path / "tickets.json"
    shutil.copy(backend.TICKETS_FILE, tmp_file)
    monkeypatch.setattr(backend, "TICKETS_FILE", tmp_file)
    return tmp_file


# ---------------------------------------------------------------------------
# tool_policy.authorize() unit tests -- pure function, no process boundary
# ---------------------------------------------------------------------------


def test_employee_may_view_own_profile():
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, *ALICE, employee_id="E1001")
    assert decision.allowed is True


def test_employee_may_not_view_others_profile():
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, *ALICE, employee_id="E1002")
    assert decision.allowed is False


def test_hr_may_view_any_profile():
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, *HELEN, employee_id="E1002")
    assert decision.allowed is True


def test_employee_may_never_view_salary_even_their_own():
    decision = authorize(tool_policy.GET_SALARY_INFO, *ALICE, employee_id="E1001")
    assert decision.allowed is False


def test_hr_may_view_salary():
    decision = authorize(tool_policy.GET_SALARY_INFO, *HELEN, employee_id="E1001")
    assert decision.allowed is True


def test_admin_may_view_salary():
    decision = authorize(tool_policy.GET_SALARY_INFO, *ADMIN, employee_id="E1002")
    assert decision.allowed is True


def test_employee_may_list_own_tickets():
    decision = authorize(tool_policy.LIST_SUPPORT_TICKETS, *BOB, employee_id="E1002")
    assert decision.allowed is True


def test_employee_may_not_list_others_tickets():
    decision = authorize(tool_policy.LIST_SUPPORT_TICKETS, *BOB, employee_id="E1001")
    assert decision.allowed is False


def test_employee_may_not_file_ticket_for_someone_else():
    decision = authorize(tool_policy.CREATE_SUPPORT_TICKET, *ALICE, employee_id="E1002")
    assert decision.allowed is False


def test_employee_may_file_own_ticket():
    decision = authorize(tool_policy.CREATE_SUPPORT_TICKET, *ALICE, employee_id="E1001")
    assert decision.allowed is True


def test_missing_identity_is_denied():
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, "", "employee", employee_id="E1001")
    assert decision.allowed is False
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, "E1001", "", employee_id="E1001")
    assert decision.allowed is False


def test_unknown_role_is_denied():
    """A role string that isn't one this system recognizes at all must be
    denied outright, not treated as some default access level -- this is
    the identity-spoofing guard: a model can put any string it wants into
    the (discarded) requester_role it proposes, but even if that somehow
    reached authorize(), an unrecognized role is never a free pass."""
    decision = authorize(tool_policy.GET_EMPLOYEE_PROFILE, "E9999", "superadmin", employee_id="E1001")
    assert decision.allowed is False


# ---------------------------------------------------------------------------
# The core authorization-boundary tests: what the MCP tools actually DO,
# not what tool_policy says they should do.
# ---------------------------------------------------------------------------


def test_get_salary_info_blocks_unauthorized_employee():
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    tool_policy.authorize() correctly says an employee may not view salary
    data -- but get_salary_info() executes the backend lookup regardless
    and returns the real record anyway. Fix mcp_server.py's
    get_salary_info() to check `decision.allowed` and return early
    (executed=False) before calling backend.find_salary() -- do not make
    this pass by changing tool_policy.py or this test.
    """
    response = srv.get_salary_info(requester_id="E1001", requester_role="employee", employee_id="E1001")
    assert response["expected_authorization"] is False
    assert response["executed"] is False, (
        "get_salary_info() executed a lookup it had already determined was unauthorized."
    )


def test_get_employee_profile_blocks_cross_employee_access():
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    Alice (E1001) asking for Bob's (E1002) profile should be denied by
    tool_policy, and get_employee_profile() should not execute the lookup.
    """
    response = srv.get_employee_profile(requester_id="E1001", requester_role="employee", employee_id="E1002")
    assert response["expected_authorization"] is False
    assert response["executed"] is False, (
        "get_employee_profile() executed a cross-employee lookup it had already determined was unauthorized."
    )


def test_list_support_tickets_blocks_cross_employee_access():
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    Bob (E1002) asking to list Alice's (E1001) tickets should be denied.
    """
    response = srv.list_support_tickets(requester_id="E1002", requester_role="employee", employee_id="E1001")
    assert response["expected_authorization"] is False
    assert response["executed"] is False, (
        "list_support_tickets() executed a cross-employee lookup it had already determined was unauthorized."
    )


def test_create_support_ticket_blocks_filing_for_someone_else(isolated_tickets_file):
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    Alice (E1001) filing a ticket "on behalf of" Bob (E1002) should be
    denied -- and critically, the write must not happen at all. This is
    the write-action analogue of the three read-only tests above: a
    deterministic control failure, not a wording problem.
    """
    response = srv.create_support_ticket(
        requester_id="E1001",
        requester_role="employee",
        subject="Test",
        description="Test",
        employee_id="E1002",
    )
    assert response["expected_authorization"] is False
    assert response["executed"] is False, (
        "create_support_ticket() executed (wrote) an unauthorized ticket on another employee's behalf."
    )


# ---------------------------------------------------------------------------
# Authorized access must keep working -- remediating must not break
# legitimate use.
# ---------------------------------------------------------------------------


def test_employee_can_view_own_profile_end_to_end():
    response = srv.get_employee_profile(requester_id="E1001", requester_role="employee", employee_id="E1001")
    assert response["expected_authorization"] is True
    assert response["executed"] is True
    assert response["result"]["employee_id"] == "E1001"


def test_hr_can_view_salary_end_to_end():
    response = srv.get_salary_info(requester_id="E1003", requester_role="hr", employee_id="E1001")
    assert response["expected_authorization"] is True
    assert response["executed"] is True
    assert "base_salary" in response["result"]


def test_employee_can_list_own_tickets_end_to_end():
    response = srv.list_support_tickets(requester_id="E1002", requester_role="employee")
    assert response["expected_authorization"] is True
    assert response["executed"] is True
    assert all(t["employee_id"] == "E1002" for t in response["result"])


# ---------------------------------------------------------------------------
# Trusted-identity injection: the host, not the model, decides who is
# calling. This exercises a real call_tool() round trip (it launches the
# actual mcp_server.py subprocess over stdio) and checks the
# argument-substitution that happens before the call is ever sent, so it
# is slower than the pure-function tests above but still asserts on the
# deterministic host-side behavior, never on model output.
# ---------------------------------------------------------------------------


def test_call_tool_discards_model_supplied_identity_arguments():
    from app.modules.module3_mcp.mcp_client import call_tool

    record = call_tool(
        tool_name=tool_policy.GET_EMPLOYEE_PROFILE,
        requester_id="E1001",
        requester_role="employee",
        arguments_requested={
            "employee_id": "E1001",
            # A model (faithfully following an injected instruction, or
            # just hallucinating) might still put these in -- they must
            # never reach the server.
            "requester_id": "E1004",
            "requester_role": "admin",
        },
    )
    assert record.arguments_sent["requester_id"] == "E1001"
    assert record.arguments_sent["requester_role"] == "employee"
