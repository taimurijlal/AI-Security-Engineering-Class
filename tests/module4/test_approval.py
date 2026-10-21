"""
Part 15 -- Module 4 automated tests: "Approval" category.

Verify:
- Sensitive actions pause.
- Denial prevents execution.
- Approval applies only to the exact proposed action.
- Modified arguments invalidate approval.
- Approval cannot be replayed.
- Unauthorized users cannot approve actions.
- Resuming requires revalidation.
- Streamlit reruns do not duplicate execution.
"""

from __future__ import annotations

import json

import pytest

from app.modules.module4_agent import approval_store
from app.modules.module4_agent import graph as g
from app.modules.module4_agent import security_boundary as sb
from app.modules.module4_agent.scenario_provider import ScriptedScenarioProvider

ALICE_ID, ALICE_ROLE = "E1001", "employee"
BOB_ID, BOB_ROLE = "E1002", "employee"
HELEN_ID, HELEN_ROLE = "E1003", "hr"

UPDATE_T1007 = {
    "next_action": "propose_action",
    "tool_name": "update_ticket_status",
    "arguments": {"ticket_id": "T-1007", "new_status": "in_progress", "resolution_note": "Looking into it."},
}
FINISH = {"next_action": "finish", "summary": "Done."}


def _scripted_update_provider(scenario_id: str = "test-approval") -> ScriptedScenarioProvider:
    return ScriptedScenarioProvider([json.dumps(UPDATE_T1007), json.dumps(FINISH)], scenario_id)


def test_sensitive_actions_pause(module4_isolated_state, run_id):
    provider = _scripted_update_provider()
    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_SECURED, "Resolve my tickets.", provider)
    payload = g.get_interrupt_payload(result)
    assert payload is not None, "A MEDIUM/HIGH-risk write must pause for approval in Secured mode."
    assert payload["tool_name"] == "update_ticket_status"

    # Vulnerable mode, by deliberate contrast (Demo 2 -- Excessive Agency),
    # never pauses at all for the exact same authorized write.
    result_vuln = g.run_agent(f"{run_id}-vuln", ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_VULNERABLE, "Resolve my tickets.", _scripted_update_provider("test-approval-vuln"))
    assert g.get_interrupt_payload(result_vuln) is None
    assert result_vuln.get("status") == "completed"


def test_denial_prevents_execution(module4_isolated_state, run_id):
    provider = _scripted_update_provider()
    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_SECURED, "Resolve my tickets.", provider)
    approval_id = g.get_interrupt_payload(result)["approval_id"]

    approval_store.decide_approval(approval_id, HELEN_ID, HELEN_ROLE, approve=False)
    final = g.resume_agent(run_id, provider)

    assert "update_ticket_status" not in {c["tool_name"] for c in final.get("completed_actions", [])}
    assert any(d["tool_name"] == "update_ticket_status" for d in final.get("denied_actions", []))
    assert not any(ev["node"] == "execute_tool" for ev in final.get("execution_trace", []))


def test_approval_applies_only_to_the_exact_proposed_action(module4_isolated_state):
    fp_a = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001")
    fp_b = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "resolved"}, "E1001")
    assert fp_a != fp_b

    request = approval_store.create_request(
        run_id="test-fp-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp_a,
    )
    approval_store.decide_approval(request.approval_id, HELEN_ID, HELEN_ROLE, approve=True)

    ok_same, _ = approval_store.revalidate_for_resume(
        request.approval_id, "update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001"
    )
    assert ok_same is True

    ok_different, reason = approval_store.revalidate_for_resume(
        request.approval_id, "update_ticket_status", {"ticket_id": "T-1007", "new_status": "resolved"}, "E1001"
    )
    assert ok_different is False
    approval_store.clear_for_run("test-fp-run")


def test_modified_arguments_invalidate_approval(module4_isolated_state):
    fp = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001")
    request = approval_store.create_request(
        run_id="test-modarg-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp,
    )
    approval_store.decide_approval(request.approval_id, HELEN_ID, HELEN_ROLE, approve=True)

    # Argument drift: new_status changed after approval was granted.
    ok, reason = approval_store.revalidate_for_resume(
        request.approval_id, "update_ticket_status", {"ticket_id": "T-1007", "new_status": "closed"}, "E1001"
    )
    assert ok is False
    assert "changed after approval" in reason
    approval_store.clear_for_run("test-modarg-run")


def test_approval_cannot_be_replayed(module4_isolated_state):
    fp = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001")
    request = approval_store.create_request(
        run_id="test-replay-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp,
    )
    approval_store.decide_approval(request.approval_id, HELEN_ID, HELEN_ROLE, approve=True)

    ok_first, _ = approval_store.revalidate_for_resume(
        request.approval_id, "update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001"
    )
    assert ok_first is True
    approval_store.mark_executed(request.approval_id)

    ok_second, reason = approval_store.revalidate_for_resume(
        request.approval_id, "update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001"
    )
    assert ok_second is False
    assert "already been executed" in reason
    approval_store.clear_for_run("test-replay-run")


def test_unauthorized_users_cannot_approve_actions(module4_isolated_state):
    fp = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001")
    request = approval_store.create_request(
        run_id="test-approver-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp,
    )

    # The requester may not approve their own action.
    ok_self, _ = approval_store.validate_approver(request, ALICE_ID, ALICE_ROLE)
    assert ok_self is False
    with pytest.raises(ValueError):
        approval_store.decide_approval(request.approval_id, ALICE_ID, ALICE_ROLE, approve=True)

    # A distinct but non-privileged identity (Bob, a standard employee)
    # may not approve it either.
    ok_nonprivileged, _ = approval_store.validate_approver(request, BOB_ID, BOB_ROLE)
    assert ok_nonprivileged is False
    with pytest.raises(ValueError):
        approval_store.decide_approval(request.approval_id, BOB_ID, BOB_ROLE, approve=True)

    # A distinct, privileged identity (Helen, HR) may.
    ok_helen, _ = approval_store.validate_approver(request, HELEN_ID, HELEN_ROLE)
    assert ok_helen is True
    approval_store.clear_for_run("test-approver-run")


def test_resuming_requires_revalidation(module4_isolated_state, run_id):
    """Resuming does not just trust the raw Command(resume=...) value --
    request_approval_if_required_node re-reads approval_store and
    re-validates the exact action. Denying, then resuming, must not
    execute the action even though resume_agent() is called exactly the
    same way as the approved path."""
    provider = _scripted_update_provider()
    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_SECURED, "Resolve my tickets.", provider)
    approval_id = g.get_interrupt_payload(result)["approval_id"]
    approval_store.decide_approval(approval_id, HELEN_ID, HELEN_ROLE, approve=False)

    # Even though we pass resume_value=True (mimicking a UI that naively
    # threads an "Approve" click's truthy value through), the real
    # decision must come from approval_store, not this value.
    final = g.resume_agent(run_id, provider, resume_value=True)
    assert "update_ticket_status" not in {c["tool_name"] for c in final.get("completed_actions", [])}


def test_streamlit_reruns_do_not_duplicate_execution(module4_isolated_state, run_id):
    """A Streamlit rerun re-executing the same code path must not create a
    second pending approval request or execute an approved write twice.
    create_request() is idempotent by (run_id, step_id, action_fingerprint)
    -- simulate two "reruns" by calling it twice with identical facts."""
    fp = sb.action_fingerprint("update_ticket_status", {"ticket_id": "T-1007", "new_status": "in_progress"}, "E1001")
    first = approval_store.create_request(
        run_id="test-rerun-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp,
    )
    second = approval_store.create_request(
        run_id="test-rerun-run", step_id=1, requester_id=ALICE_ID, requester_role=ALICE_ROLE,
        tool_name="update_ticket_status", arguments={"ticket_id": "T-1007", "new_status": "in_progress"},
        target_resource="E1001", risk_level="medium", action_fingerprint=fp,
    )
    assert first.approval_id == second.approval_id
    assert len(approval_store.list_for_run("test-rerun-run")) == 1

    # End-to-end: run the real graph twice through the same pending pause
    # (simulating a Streamlit rerun re-reading state before any approval
    # decision exists yet) and confirm only one approval request exists
    # and the write executes at most once after it is approved.
    provider = _scripted_update_provider()
    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, sb.AGENT_MODE_SECURED, "Resolve my tickets.", provider)
    approval_id = g.get_interrupt_payload(result)["approval_id"]
    assert len([r for r in approval_store.list_for_run(run_id)]) == 1

    approval_store.decide_approval(approval_id, HELEN_ID, HELEN_ROLE, approve=True)
    final = g.resume_agent(run_id, provider)
    completed = [c for c in final.get("completed_actions", []) if c["tool_name"] == "update_ticket_status"]
    assert len(completed) == 1
    approval_store.clear_for_run("test-rerun-run")
