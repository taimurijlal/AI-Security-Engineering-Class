"""
Part 15 -- Module 4 automated tests: "Execution budgets" category.

Verify:
- Maximum steps are enforced.
- Maximum tool calls are enforced.
- Retry limits are enforced.
- Repeated failures stop safely.
"""

from __future__ import annotations

from app.modules.module4_agent import graph as g
from app.modules.module4_agent import scenario_provider as scen
from app.modules.module4_agent import security_boundary as sb

ALICE_ID, ALICE_ROLE = "E1001", "employee"


def test_maximum_steps_are_enforced():
    limits = sb.budget_limits(sb.AGENT_MODE_SECURED)
    reason = sb.budget_exceeded(limits.max_steps, 0, 0, limits)
    assert reason is not None
    assert "Step budget exceeded" in reason
    assert sb.budget_exceeded(limits.max_steps - 1, 0, 0, limits) is None

    vuln_limits = sb.budget_limits(sb.AGENT_MODE_VULNERABLE)
    assert vuln_limits.max_steps == sb.ABSOLUTE_MAX_STEPS
    assert sb.budget_exceeded(sb.ABSOLUTE_MAX_STEPS, 0, 0, vuln_limits) is not None


def test_maximum_tool_calls_are_enforced():
    limits = sb.budget_limits(sb.AGENT_MODE_SECURED)
    reason = sb.budget_exceeded(0, limits.max_tool_calls, 0, limits)
    assert reason is not None
    assert "Tool-call budget exceeded" in reason
    assert sb.budget_exceeded(0, limits.max_tool_calls - 1, 0, limits) is None


def test_retry_limits_are_enforced():
    limits = sb.budget_limits(sb.AGENT_MODE_SECURED)
    reason = sb.budget_exceeded(0, 0, limits.max_retries_per_tool, limits)
    assert reason is not None
    assert "Retry budget exceeded" in reason
    assert sb.budget_exceeded(0, 0, limits.max_retries_per_tool - 1, limits) is None


def test_vulnerable_mode_still_has_a_finite_ceiling():
    """Even Vulnerable mode -- which disables the approval gate (Demo 2)
    -- must have an absolute, non-configurable safety ceiling (Part 10
    Demo 5 Scenario B, Part 17): it is larger than Secured mode's, but
    never unlimited."""
    vuln = sb.budget_limits(sb.AGENT_MODE_VULNERABLE)
    secured = sb.budget_limits(sb.AGENT_MODE_SECURED)
    assert vuln.max_steps > secured.max_steps
    assert vuln.max_tool_calls > secured.max_tool_calls
    assert vuln.max_retries_per_tool > secured.max_retries_per_tool
    assert vuln.max_steps == sb.ABSOLUTE_MAX_STEPS  # finite, not infinite


def test_repeated_failures_stop_safely(module4_isolated_state, run_id):
    """Demo 5b (Runaway Execution): the scripted planner repeatedly
    proposes updating a nonexistent ticket (T-9999). Every attempt is
    denied (no ticket found) and nothing ever executes; the run must stop
    itself via the real execution budget -- not loop forever, not crash,
    and not fabricate a 'completed' result."""
    scenario = scen.SCENARIOS["demo5b_runaway_execution"]
    provider = scen.build_provider("demo5b_runaway_execution")

    result = g.run_agent(run_id, ALICE_ID, ALICE_ROLE, scenario.recommended_agent_mode, scenario.goal, provider)

    assert result.get("status") == "stopped"
    assert "budget" in (result.get("stop_reason") or "").lower()
    assert result.get("completed_actions") == []
    assert len(result.get("denied_actions", [])) > 0

    checklist = dict(scen.evidence_checklist("demo5b_runaway_execution", result))
    assert checklist["Absolute execution budget enforced"] == "Yes"
    assert "as expected" in checklist["Any action ever actually executed"].lower()
