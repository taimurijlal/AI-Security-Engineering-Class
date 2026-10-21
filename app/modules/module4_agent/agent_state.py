"""
Typed state for Module 4's LangGraph agent.

Every field here is either (a) set once from the trusted application
session before the graph starts and never mutated by a node that reads
model output (run_id, requester_id, requester_role, agent_mode), or (b)
written only by this package's own deterministic nodes based on real
observations (authorization_result, risk_level, tool_observations,
execution_trace, ...). No field here is ever assigned directly from raw
model text -- model text is always parsed into a small, validated shape
first (see graph.py's _parse_plan/_parse_action), which is what Part 4
means by "structured action proposals, not free-form text."
"""

from __future__ import annotations

from typing import Any, TypedDict


class ProposedAction(TypedDict, total=False):
    tool_name: str
    arguments: dict[str, Any]
    target_employee_id: str | None


class TraceEvent(TypedDict):
    step: int
    node: str
    summary: str
    detail: dict[str, Any]


class AgentState(TypedDict, total=False):
    # Identity and run bookkeeping -- requester_id/requester_role are
    # injected from app.core.security.User by the caller before the first
    # invoke() and are NEVER written by any node that consumes model output.
    run_id: str
    requester_id: str
    requester_role: str
    agent_mode: str  # "vulnerable" | "secured" -- see security_boundary.AGENT_MODE_*

    # Goal
    goal: str
    goal_valid: bool
    goal_scope: str  # security_boundary.GoalScope value

    # Planner conversation state
    messages: list[dict[str, str]]
    current_step: str
    next_action: str | None  # "retrieve_knowledge" | "propose_action" | "finish" | None

    # Execution budget
    step_count: int
    tool_call_count: int
    retry_count: int
    stop_reason: str | None

    # In-flight proposal / authorization / risk / approval
    proposed_action: ProposedAction | None
    pending_query: str | None
    authorization_result: dict[str, Any] | None
    risk_level: str | None
    approval_required: bool
    approval_id: str | None
    approval_state: str | None  # "not_required" | "pending" | "approved" | "denied"

    # Observations and evidence
    retrieved_documents: list[dict[str, Any]]
    tool_observations: list[dict[str, Any]]
    memory_refs: list[str]
    completed_actions: list[dict[str, Any]]
    denied_actions: list[dict[str, Any]]
    execution_trace: list[TraceEvent]

    # Outcome
    final_result: str | None
    status: str  # "running" | "awaiting_approval" | "completed" | "rejected" | "stopped"


def new_state(run_id: str, requester_id: str, requester_role: str, agent_mode: str, goal: str) -> AgentState:
    return AgentState(
        run_id=run_id,
        requester_id=requester_id,
        requester_role=requester_role,
        agent_mode=agent_mode,
        goal=goal,
        goal_valid=False,
        goal_scope="self",
        messages=[],
        current_step="start",
        next_action=None,
        step_count=0,
        tool_call_count=0,
        retry_count=0,
        stop_reason=None,
        proposed_action=None,
        pending_query=None,
        authorization_result=None,
        risk_level=None,
        approval_required=False,
        approval_id=None,
        approval_state=None,
        retrieved_documents=[],
        tool_observations=[],
        memory_refs=[],
        completed_actions=[],
        denied_actions=[],
        execution_trace=[],
        final_result=None,
        status="running",
    )
