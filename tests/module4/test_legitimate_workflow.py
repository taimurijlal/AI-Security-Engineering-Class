"""
Part 15 -- Module 4 automated tests: "Legitimate workflow" category.

Verify:
- Alice can access her own tickets.
- Authorized RAG retrieval works.
- Authorized MCP tools work.
- The agent can complete a multi-step workflow.
- Execution events reflect actual actions.

These tests never assert on model wording -- consistent with Modules
1-3's own testing philosophy (see tests/module3/test_mcp_security.py's
docstring) -- and never fabricate a result: every assertion below reads
back this run's own real, already-produced AgentState.
"""

from __future__ import annotations

from app.modules.module2_rag import rag as rag_module
from app.modules.module3_mcp import mcp_client, tool_policy
from app.modules.module4_agent import graph as g
from app.modules.module4_agent import scenario_provider as scen
from app.modules.module4_agent import security_boundary as sb

ALICE_ID, ALICE_ROLE = "E1001", "employee"


def test_alice_can_access_her_own_tickets():
    """Direct MCP boundary check: Alice listing her own tickets is
    authorized by the real tool_policy.authorize() rule, and the
    agent-layer re-derivation (security_boundary.check_mcp_authorization)
    agrees."""
    decision = tool_policy.authorize(tool_policy.LIST_SUPPORT_TICKETS, ALICE_ID, ALICE_ROLE, employee_id=ALICE_ID)
    assert decision.allowed is True

    check = sb.check_mcp_authorization(tool_policy.LIST_SUPPORT_TICKETS, ALICE_ID, ALICE_ROLE, employee_id=ALICE_ID)
    assert check.allowed is True


def test_alice_can_access_her_own_tickets_end_to_end(module4_isolated_state):
    """Real MCP round trip (genuine subprocess, per mcp_client.py) for
    Alice's own tickets: the server actually returns her ticket records."""
    record = mcp_client.call_tool(tool_policy.LIST_SUPPORT_TICKETS, ALICE_ID, ALICE_ROLE, {})
    assert record.executed is True
    assert record.is_error is False
    assert isinstance(record.result, list)
    assert all(t["employee_id"] == ALICE_ID for t in record.result)
    assert len(record.result) > 0


def test_authorized_rag_retrieval_works():
    """A document whose allowed_roles includes 'employee' (e.g. the
    employee handbook / IT security policy / quarterly update) must
    survive the agent's own independent RAG re-check
    (security_boundary.filter_authorized_chunks), which is always
    enforced in both agent modes regardless of Module 2's own retrieval
    fix status."""
    candidates = rag_module.retrieve_documents("employee handbook policy", ALICE_ROLE)
    authorized = sb.filter_authorized_chunks(candidates, ALICE_ROLE)
    assert len(authorized) > 0
    for item in authorized:
        assert rag_module.is_authorized(ALICE_ROLE, item.chunk.allowed_roles) is True


def test_authorized_mcp_tools_work():
    """Alice updating her own ticket's status is a correctly-authorized
    write at the real tool_policy layer."""
    decision = tool_policy.authorize(
        tool_policy.UPDATE_TICKET_STATUS,
        ALICE_ID,
        ALICE_ROLE,
        employee_id=ALICE_ID,
        new_status="in_progress",
        current_status="open",
    )
    assert decision.allowed is True


def test_agent_completes_multistep_workflow_secured_mode(module4_isolated_state, run_id):
    """Demo 1 (Legitimate Autonomous Workflow): retrieve knowledge, list
    Alice's own tickets, update one, finish -- in Secured mode the write
    pauses for approval first. Drives the real graph end to end, approves
    via the real approval_store, and resumes -- never fabricates a
    'completed' outcome."""
    provider = scen.build_provider("demo1_legitimate_workflow")
    scenario = scen.SCENARIOS["demo1_legitimate_workflow"]

    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, scenario.recommended_agent_mode, scenario.goal, provider)

    interrupt_payload = g.get_interrupt_payload(result)
    assert interrupt_payload is not None, "Secured mode should pause for approval before the ticket write executes."
    approval_id = interrupt_payload["approval_id"]

    from app.modules.module4_agent import approval_store

    ok, _ = approval_store.validate_approver(approval_store.get(approval_id), "E1003", "hr")
    assert ok is True
    approval_store.decide_approval(approval_id, "E1003", "hr", approve=True)

    result = g.resume_agent(run_id, provider)
    final_state = g.get_run_state(run_id) or result

    assert final_state.get("status") == "completed"
    assert "update_ticket_status" in {c["tool_name"] for c in final_state.get("completed_actions", [])}
    assert final_state.get("denied_actions") == []


def test_execution_events_reflect_actual_actions(module4_isolated_state, run_id):
    """The execution_trace recorded for a run must correspond to the
    tool(s) actually dispatched through mcp_client -- not a narrative
    description of what was *supposed* to happen."""
    provider = scen.build_provider("demo1_legitimate_workflow")
    scenario = scen.SCENARIOS["demo1_legitimate_workflow"]
    g.run_agent(run_id, ALICE_ID, ALICE_ROLE, scenario.recommended_agent_mode, scenario.goal, provider)

    from app.modules.module4_agent import approval_store

    pending = [r for r in approval_store.list_for_run(run_id) if r.status == approval_store.PENDING]
    for request in pending:
        approval_store.decide_approval(request.approval_id, "E1003", "hr", approve=True)
    g.resume_agent(run_id, provider)

    state = g.get_run_state(run_id)
    assert state is not None
    trace = state.get("execution_trace", [])
    execute_events = [ev for ev in trace if ev["node"] == "execute_tool"]
    assert len(execute_events) >= 1
    for ev in execute_events:
        # Every recorded execute_tool trace event's tool_name must also
        # appear in tool_observations -- the real, observed MCP response --
        # never only in the trace's own narration.
        tool_name = ev["detail"]["tool_name"]
        assert any(obs["tool_name"] == tool_name for obs in state.get("tool_observations", []))
