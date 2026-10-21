"""
Part 15 -- Module 4 automated tests: "Authorization" category.

Verify:
- Alice cannot retrieve Bob's salary.
- Alice cannot retrieve restricted Project Atlas information.
- Alice cannot modify another employee's tickets.
- Denied actions never reach protected execution.
- Broad goals cannot expand permissions.

These exercise the agent-layer boundary (app.modules.module4_agent.
security_boundary) independently of whatever state Module 2/3's own
retrieval/MCP-server enforcement happens to be in (per
security_boundary.py's own docstring: Module 4 must demonstrate its
boundary "without ever disabling, bypassing, or depending on the current
state of Module 2/3's own enforcement"). "Denied actions never reach
protected execution" is therefore checked by asserting the real
AgentState's execution_trace never contains an execute_tool event for
the denied tool -- not by trusting mcp_server.py's own gating, which is
Module 3's separate, independently-gradable lab exercise.
"""

from __future__ import annotations

import json

from app.modules.module2_rag import rag as rag_module
from app.modules.module3_mcp import tool_policy
from app.modules.module4_agent import graph as g
from app.modules.module4_agent import security_boundary as sb
from app.modules.module4_agent.scenario_provider import ScriptedScenarioProvider

ALICE_ID, ALICE_ROLE = "E1001", "employee"
BOB_ID, BOB_ROLE = "E1002", "employee"


def test_alice_cannot_retrieve_bobs_salary():
    decision = tool_policy.authorize(tool_policy.GET_SALARY_INFO, ALICE_ID, ALICE_ROLE, employee_id=ALICE_ID)
    assert decision.allowed is False  # salary is HR/Admin-only regardless of whose record it is

    check = sb.check_mcp_authorization(tool_policy.GET_SALARY_INFO, ALICE_ID, ALICE_ROLE, employee_id=BOB_ID)
    assert check.allowed is False


def test_alice_cannot_retrieve_restricted_project_atlas_information():
    """project_atlas.txt is ALLOWED_ROLES: executive -- 'employee' must be
    filtered out by the agent's own independent RAG re-check.

    The candidate pool is every chunk in the index, built directly rather
    than through retrieve_documents(): once a student fixes Module 2,
    retrieve_documents() stops returning the restricted chunk at all, and
    this test must keep exercising Module 4's own boundary either way."""
    candidates = [
        rag_module.RetrievedChunk(chunk=chunk, similarity=0.0, authorized=True)
        for chunk in rag_module.get_index().chunks
    ]
    assert any(item.chunk.title.startswith("Project Atlas") for item in candidates), (
        "Test setup assumption failed: the restricted document was not even a retrieval candidate."
    )
    authorized = sb.filter_authorized_chunks(candidates, ALICE_ROLE)
    assert not any(item.chunk.title.startswith("Project Atlas") for item in authorized)


def test_alice_cannot_modify_another_employees_tickets():
    decision = tool_policy.authorize(
        tool_policy.UPDATE_TICKET_STATUS,
        ALICE_ID,
        ALICE_ROLE,
        employee_id=BOB_ID,
        new_status="in_progress",
        current_status="open",
    )
    assert decision.allowed is False

    check = sb.check_mcp_authorization(
        tool_policy.UPDATE_TICKET_STATUS,
        ALICE_ID,
        ALICE_ROLE,
        employee_id=BOB_ID,
        new_status="in_progress",
        current_status="open",
    )
    assert check.allowed is False


def test_denied_actions_never_reach_protected_execution(module4_isolated_state, run_id):
    """A scripted planner that proposes get_salary_info for Bob (Alice is
    not HR/Admin) must be denied at authorize_action_node and must never
    reach execute_tool_node -- i.e. no real MCP call is ever dispatched
    for it."""
    script = [
        json.dumps({"next_action": "propose_action", "tool_name": "get_salary_info", "arguments": {"employee_id": BOB_ID}}),
        json.dumps({"next_action": "finish", "summary": "Attempted salary lookup was blocked; nothing else to do."}),
    ]
    provider = ScriptedScenarioProvider(script, "test-unauthorized-salary")

    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_SECURED, "Check compensation details.", provider)

    assert result.get("status") == "completed"
    denied_tools = {d["tool_name"] for d in result.get("denied_actions", [])}
    assert "get_salary_info" in denied_tools

    trace = result.get("execution_trace", [])
    assert not any(ev["node"] == "execute_tool" for ev in trace), (
        "execute_tool_node must never run for a denied proposal."
    )
    assert not any(obs["tool_name"] == "get_salary_info" for obs in result.get("tool_observations", []))
    assert not any(c["tool_name"] == "get_salary_info" for c in result.get("completed_actions", []))


def test_broad_goals_cannot_expand_permissions():
    """A broad-sounding goal from a standard employee is still constrained
    to GoalScope.SELF -- this is a correctness property, not a toggle, so
    it must hold regardless of agent mode (checked directly here; the
    end-to-end demo5a scenario exercises the same control through the
    full graph in test_execution_budgets.py's sibling scenario tests)."""
    validation = sb.validate_goal("Resolve all outstanding employee requests.", "employee")
    assert validation.allowed is True
    assert validation.scope == sb.GoalScope.SELF

    # A privileged role, by contrast, IS deliberately grantable BROAD scope
    # for the same broad phrasing -- this asymmetry is the point.
    privileged_validation = sb.validate_goal("Resolve all outstanding employee requests.", "hr")
    assert privileged_validation.scope == sb.GoalScope.BROAD

    # And the scope grant alone is not enough -- action_within_scope() is
    # re-checked per proposed action, independently of the goal text.
    assert sb.action_within_scope(sb.GoalScope.SELF, ALICE_ID, BOB_ID) is False
    assert sb.action_within_scope(sb.GoalScope.SELF, ALICE_ID, ALICE_ID) is True
    assert sb.action_within_scope(sb.GoalScope.BROAD, ALICE_ID, BOB_ID) is True
