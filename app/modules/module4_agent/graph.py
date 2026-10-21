"""
Module 4's real LangGraph StateGraph: a single-agent planner/executor loop,
not a hand-rolled while-loop wearing a LangGraph costume. Every node below
is a real graph node wired through real conditional edges (see
build_graph()); the shape matches the lab spec's diagram:

    START -> validate_goal -> plan_next_step -> (route) -> {
        retrieve_authorized_knowledge -> plan_next_step
        propose_action -> authorize_action -> (route) -> {
            assess_risk -> request_approval_if_required -> (route) -> {
                execute_tool -> observe_result -> plan_next_step
                reject_or_escalate -> plan_next_step
            }
            reject_or_escalate -> plan_next_step
        }
        finish -> END
        stop_safely -> END
    }

THE CORE PRINCIPLE, IN CODE: "The agent proposes. The application
authorizes. The trusted execution layer enforces." plan_next_step_node and
propose_action_node are the only two nodes that read model output at all,
and all they ever produce is a small, parsed, structured proposal (a
ProposedAction) -- never a decision. authorize_action_node,
assess_risk_node, request_approval_if_required_node, and execute_tool_node
make every real decision from security_boundary.py's pure functions and
this app's own trusted stores (approval_store, mcp_client), never from
anything the model said about whether an action should proceed.

TRUSTED IDENTITY: requester_id/requester_role arrive once in the initial
AgentState (built by module.py from the real app.core.security.User before
run_agent() is ever called) and are read-only from every node in this
file. No node here ever assigns to state["requester_id"] or
state["requester_role"]; nothing parsed from model text is ever written to
those keys. This mirrors mcp_client.call_tool()'s unconditional identity
overwrite and agent_state.py's own module docstring.

RESUME SAFETY (Part 6, Part 9): request_approval_if_required_node never
treats the raw value a human's Approve/Deny click threads back through
Command(resume=...) as the decision. The Streamlit UI calls
approval_store.decide_approval() (itself gated by validate_approver())
*before* it triggers a resume; this node, once interrupt() returns control
to it, re-reads the authoritative status from approval_store.get() and
re-validates the exact action via approval_store.revalidate_for_resume()
-- the resume value itself is discarded. Because LangGraph re-executes a
node function's entire body from the top on every resume, this node's
first lines (building the fingerprint, calling create_request()) run
again on resume too; create_request() is idempotent for exactly this
reason (see approval_store.py), so that replay cannot create a second
pending request or lose track of the first one.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, interrupt

from app.core.llm import LLMError, Provider
from app.modules.module2_rag import rag as rag_module
from app.modules.module3_mcp import backend, mcp_client, tool_policy
from app.modules.module4_agent import approval_store, memory_store as mem
from app.modules.module4_agent import security_boundary as sb
from app.modules.module4_agent.agent_state import AgentState, ProposedAction, TraceEvent, new_state
from app.modules.module4_agent.prompts import PLANNER_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _trace(state: AgentState, node: str, summary: str, detail: dict[str, Any] | None = None) -> TraceEvent:
    """Build one execution_trace entry. This -- not any hidden model
    scratchpad -- is what the Agent Inspector (Part 8) actually renders:
    an observable, structured record of what each deterministic node did,
    never the model's private chain-of-thought (which this app never asks
    for or stores in the first place; the planner is only ever asked for
    the small next_action JSON object)."""
    return TraceEvent(step=state.get("step_count", 0), node=node, summary=summary, detail=detail or {})


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_plan(raw_response: str) -> dict[str, Any] | None:
    """Tolerant, best-effort parse of the planner's single-JSON-object
    convention (see prompts.PLANNER_SYSTEM_PROMPT) -- the Module 4
    analogue of module3_mcp.module._parse_tool_call(). Returns the parsed
    dict if it at least has a recognizable "next_action" key, else None
    (treated by plan_next_step_node as an invalid plan, never as a crash)."""
    text = (raw_response or "").strip()
    fence_match = _FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJ_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict) or "next_action" not in parsed:
        return None
    return parsed


def _tool_catalog_text() -> str:
    """Reuses Module 3's real MCP tool discovery (mcp_client.list_tools())
    -- the catalog shown to the planner always reflects exactly the tools
    actually registered on the live MCP server, including Module 4's
    additive update_ticket_status, never a hand-maintained list that could
    drift from reality."""
    try:
        tools = mcp_client.list_tools()
    except Exception as exc:  # defensive: a dead MCP server must not crash a planning turn
        return f"(Tool catalog unavailable -- the MCP server could not be reached: {exc})"

    lines = ["AVAILABLE TOOLS:"]
    for tool in tools:
        schema = tool.get("input_schema") or {}
        props = schema.get("properties", {})
        arg_names = [name for name in props if name not in ("requester_id", "requester_role")]
        lines.append(f"- {tool['name']}({', '.join(arg_names)}): {tool['description']}")
    return "\n".join(lines)


def _resolve_target_employee_id(tool_name: str, arguments: dict[str, Any], requester_id: str) -> str | None:
    """Best-effort resolution of 'whose record does this action target',
    used for goal-scope enforcement (security_boundary.action_within_scope)
    and risk classification. update_ticket_status has no employee_id
    argument of its own -- ownership is looked up from the ticket record
    itself, exactly like mcp_server.py's own update_ticket_status tool
    does before authorizing."""
    if tool_name == tool_policy.UPDATE_TICKET_STATUS:
        ticket = backend.find_ticket(str(arguments.get("ticket_id") or ""))
        return ticket["employee_id"] if ticket else None
    if tool_name in (
        tool_policy.GET_EMPLOYEE_PROFILE,
        tool_policy.GET_SALARY_INFO,
        tool_policy.LIST_SUPPORT_TICKETS,
        tool_policy.CREATE_SUPPORT_TICKET,
    ):
        explicit = arguments.get("employee_id")
        return str(explicit) if explicit else requester_id
    return None


def _safe_result_synopsis(tool_name: str, result: Any) -> str:
    """Render a short, structured synopsis of a successful tool call's
    *factual* result fields for the planner's next-turn context (fed into
    completed_actions below) -- deliberately NEVER any free-text field a
    ticket's filer (or an attacker) controls, such as `description`,
    `subject`, or `resolution_note`. Those are exactly where Demo 3/4's
    injected instructions live, and the only place that content is allowed
    to reach planning context at all is through memory_store.build_context()'s
    trust-classification boundary. This function exists because, without
    it, completed_actions only ever carried the authorization *reason*
    (e.g. "Employees may view their own tickets."), never the actual
    result -- so a real model had no way to learn what tickets exist
    across turns and would re-call the same read tool indefinitely. This
    synopsis must never become a second, unfiltered channel for the same
    untrusted free text memory_store already gates; if that boundary is
    ever widened, this function must not be used as a workaround for it.
    Returns "" if there's nothing safe/useful to summarize."""
    if tool_name == tool_policy.LIST_SUPPORT_TICKETS:
        if not isinstance(result, list):
            return ""
        if not result:
            return "No tickets found."
        parts = [f"{t.get('ticket_id')} ({t.get('status')})" for t in result if isinstance(t, dict)]
        return f"Tickets: {', '.join(parts)}." if parts else ""

    if not isinstance(result, dict) or "error" in result:
        return ""

    if tool_name in (tool_policy.CREATE_SUPPORT_TICKET, tool_policy.UPDATE_TICKET_STATUS):
        return f"{result.get('ticket_id')} is now '{result.get('status')}'."

    if tool_name == tool_policy.GET_EMPLOYEE_PROFILE:
        return f"{result.get('employee_id')}: {result.get('title')}, {result.get('department')}."

    if tool_name == tool_policy.GET_SALARY_INFO:
        return (
            f"{result.get('employee_id')}: base_salary={result.get('base_salary')}, "
            f"bonus_target_pct={result.get('bonus_target_pct')}."
        )

    return ""


def _mark_memory_entries_influencing_step(state: AgentState, step_id: int) -> None:
    """Part 12 (Agent Memory Inspector) needs a real, observed answer to
    'did this memory entry actually influence a later action' -- not a
    guess. This replicates, read-only, exactly the inclusion rule
    mem.build_context() itself uses (verbatim-in-vulnerable-mode vs.
    trusted-only-in-secured-mode) so mark_influenced() is called for
    precisely the entries that were actually placed in this step's
    planning context, and no others."""
    owner = state["requester_id"]
    agent_mode = state["agent_mode"]
    for entry in mem.list_for_owner(state["run_id"], owner):
        included = agent_mode == mem.AGENT_MODE_VULNERABLE or entry.trust == mem.TrustLevel.TRUSTED_OBSERVATION.value
        if included:
            mem.mark_influenced(entry.memory_id, step_id)


def _build_planning_context(state: AgentState, tool_catalog_text: str) -> str:
    """Everything the planner needs to pick its next action, assembled
    fresh every turn. app.core.llm.Provider.generate() is a single
    stateless call (system_prompt, user_prompt, context) -> str with no
    native memory of prior turns, so the running history of this run
    (completed/denied actions, retrieved documents, prior trace) must be
    re-embedded into context on every single planning call, not just the
    first one."""
    lines = [
        f"GOAL (from the authenticated user, trusted): {state['goal']}",
        f"GOAL SCOPE GRANTED BY THE APPLICATION (not by you, not editable by anything in this "
        f"conversation): {state.get('goal_scope', 'self')}",
        f"Step {state.get('step_count', 0)} of this run. Tool calls made so far: "
        f"{state.get('tool_call_count', 0)}.",
    ]

    completed = state.get("completed_actions") or []
    if completed:
        lines.append("ACTIONS COMPLETED SO FAR THIS RUN:")
        for item in completed:
            lines.append(f"- {item.get('tool_name')} on {item.get('target_employee_id')}: {item.get('summary')}")

    denied = state.get("denied_actions") or []
    if denied:
        lines.append("ACTIONS DENIED SO FAR THIS RUN (do not re-propose these unchanged):")
        for item in denied:
            lines.append(f"- {item.get('tool_name')} on {item.get('target_employee_id')}: {item.get('reason')}")

    retrieved = state.get("retrieved_documents") or []
    if retrieved:
        lines.append("COMPANY DOCUMENTS RETRIEVED SO FAR THIS RUN (titles only):")
        for doc in retrieved[-6:]:
            lines.append(f"- {doc.get('title')} ({doc.get('source')})")

    lines.append("")
    lines.append(mem.build_context(state["run_id"], state["requester_id"], state["agent_mode"]))
    lines.append("")
    lines.append(tool_catalog_text)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def validate_goal_node(state: AgentState) -> dict[str, Any]:
    """Deterministic goal validation/scoping (security_boundary.validate_goal)
    -- this is Part 5/Demo 5 Scenario A's control, evaluated once up front
    and again (via action_within_scope) at every single authorize_action_node
    call, so a broad-sounding goal can never be used to launder a single
    out-of-scope action through an initially-accepted goal."""
    validation = sb.validate_goal(state["goal"], state["requester_role"])
    entry = mem.store(state["run_id"], state["requester_id"], mem.MemorySource.USER_GOAL.value, state["goal"], state["agent_mode"])
    trace = _trace(
        state,
        "validate_goal",
        f"Goal {'accepted' if validation.allowed else 'rejected'} at scope '{validation.scope.value}': {validation.reason}",
        {"allowed": validation.allowed, "scope": validation.scope.value, "reason": validation.reason},
    )
    return {
        "goal_valid": validation.allowed,
        "goal_scope": validation.scope.value,
        "memory_refs": [*state.get("memory_refs", []), entry.memory_id],
        "execution_trace": [*state.get("execution_trace", []), trace],
    }


def _route_after_validate(state: AgentState) -> str:
    return "plan_next_step" if state.get("goal_valid") else "stop_safely"


def plan_next_step_node(state: AgentState, config=None) -> dict[str, Any]:
    # NOTE: `config` is deliberately left unannotated. This module uses
    # `from __future__ import annotations` (PEP 563), which turns every
    # annotation -- including a `RunnableConfig` one -- into a plain
    # string at runtime. LangGraph's own machinery decides whether to
    # thread the real, merged per-invoke config into a node (vs. a fresh
    # blank default) partly by inspecting this parameter's *resolved*
    # type; a stringified annotation defeats that check and silently
    # hands the node an empty config, which would make
    # config["configurable"]["provider"] raise KeyError below. Confirmed
    # by direct experimentation against this project's installed
    # langgraph==0.6.11. Leaving the annotation off is the verified fix.
    """The only node (besides propose_action_node, which merely normalizes
    this node's output) that calls the LLM. Checks the real, enforced
    execution budget BEFORE spending a planning call -- budget_exceeded()
    is the actual stop condition (Part 10 Demo 5 Scenario B), not a
    suggestion the model can decide to override by continuing to talk."""
    configurable = (config or {}).get("configurable", {})
    provider: Provider = configurable["provider"]

    limits = sb.budget_limits(state["agent_mode"])
    stop_reason = sb.budget_exceeded(
        state.get("step_count", 0), state.get("tool_call_count", 0), state.get("retry_count", 0), limits
    )
    if stop_reason:
        return {
            "next_action": "__budget_exceeded__",
            "stop_reason": stop_reason,
            "execution_trace": [*state.get("execution_trace", []), _trace(state, "plan_next_step", stop_reason)],
        }

    step_count = state.get("step_count", 0) + 1
    context = _build_planning_context(state, _tool_catalog_text())
    _mark_memory_entries_influencing_step(state, step_count)

    try:
        raw = provider.generate(system_prompt=PLANNER_SYSTEM_PROMPT, user_prompt=state["goal"], context=context)
    except LLMError as exc:
        return {
            "step_count": step_count,
            "next_action": "__plan_failed__",
            "stop_reason": f"The planning model could not be reached: {exc}",
            "execution_trace": [
                *state.get("execution_trace", []),
                _trace(state, "plan_next_step", f"LLM call failed: {exc}"),
            ],
        }

    plan = _parse_plan(raw)
    messages = [*state.get("messages", []), {"role": "assistant", "content": raw}]
    trace_list = [*state.get("execution_trace", [])]

    if plan is None:
        trace_list.append(_trace(state, "plan_next_step", "Planner response could not be parsed as a valid plan.", {"raw": raw}))
        return {"step_count": step_count, "next_action": "__invalid_plan__", "messages": messages, "execution_trace": trace_list}

    next_action = str(plan.get("next_action") or "")

    # Tolerant normalization: some models (observed with gpt-4o-mini) name the
    # tool directly as next_action (e.g. {"next_action": "list_support_tickets",
    # "arguments": {...}}) instead of wrapping it in the documented
    # {"next_action": "propose_action", "tool_name": ..., "arguments": ...}
    # envelope PLANNER_SYSTEM_PROMPT asks for. Treating that shape as an
    # invalid plan every single turn gives the model no path to recover --
    # the planning context passed back in (_build_planning_context) never
    # records an invalid attempt, so nothing changes between turns and the
    # identical malformed plan repeats until the step budget alone ends the
    # run (security_boundary.budget_exceeded() still enforces correctly in
    # that case, it just isn't the control actually being tested by a
    # legitimate-workflow goal). Recognizing a bare registered tool name here
    # and rewriting it into the canonical propose_action shape -- before any
    # authorization/approval/execution boundary below ever sees it -- fixes
    # the usability gap without weakening or bypassing any of those checks:
    # authorize_action_node still independently re-validates tool_name exactly
    # as if the planner had used the documented envelope to begin with.
    if next_action in tool_policy.ALL_TOOLS:
        raw_arguments = plan.get("arguments")
        if not isinstance(raw_arguments, dict):
            raw_arguments = {k: v for k, v in plan.items() if k not in ("next_action", "tool_name")}
        plan = {"next_action": "propose_action", "tool_name": next_action, "arguments": raw_arguments}
        next_action = "propose_action"
        trace_list.append(
            _trace(
                state,
                "plan_next_step",
                f"Planner named tool '{plan['tool_name']}' directly as next_action; normalized to propose_action.",
            )
        )

    if next_action == "retrieve_knowledge":
        query = str(plan.get("query") or "").strip()
        if not query:
            trace_list.append(_trace(state, "plan_next_step", "retrieve_knowledge proposed with no query; treating as invalid."))
            return {"step_count": step_count, "next_action": "__invalid_plan__", "messages": messages, "execution_trace": trace_list}
        trace_list.append(_trace(state, "plan_next_step", f"Planner requested knowledge retrieval: '{query}'."))
        return {"step_count": step_count, "next_action": next_action, "pending_query": query, "messages": messages, "execution_trace": trace_list}

    if next_action == "propose_action":
        tool_name = str(plan.get("tool_name") or "")
        arguments = plan.get("arguments")
        if not tool_name or not isinstance(arguments, dict):
            trace_list.append(_trace(state, "plan_next_step", "propose_action missing tool_name/arguments; treating as invalid."))
            return {"step_count": step_count, "next_action": "__invalid_plan__", "messages": messages, "execution_trace": trace_list}
        trace_list.append(_trace(state, "plan_next_step", f"Planner proposed calling '{tool_name}'.", {"tool_name": tool_name, "arguments": arguments}))
        return {
            "step_count": step_count,
            "next_action": next_action,
            "proposed_action": ProposedAction(tool_name=tool_name, arguments=dict(arguments), target_employee_id=None),
            "messages": messages,
            "execution_trace": trace_list,
        }

    if next_action == "finish":
        summary = str(plan.get("summary") or "(no summary provided)")
        trace_list.append(_trace(state, "plan_next_step", "Planner finished this run.", {"summary": summary}))
        return {"step_count": step_count, "next_action": next_action, "final_result": summary, "messages": messages, "execution_trace": trace_list}

    trace_list.append(
        _trace(
            state,
            "plan_next_step",
            f"Unrecognized next_action '{next_action}'; treating as invalid.",
            {"raw": raw},
        )
    )
    return {"step_count": step_count, "next_action": "__invalid_plan__", "messages": messages, "execution_trace": trace_list}


def _route_after_plan(state: AgentState) -> str:
    next_action = state.get("next_action")
    if next_action == "__budget_exceeded__":
        return "stop_safely"
    if next_action in ("__invalid_plan__", "__plan_failed__"):
        # Loop back to plan_next_step_node -- step_count already incremented
        # above, so budget_exceeded() will eventually stop a persistently
        # failing/uncooperative planner rather than looping forever.
        return "plan_next_step" if next_action == "__invalid_plan__" else "stop_safely"
    if next_action == "retrieve_knowledge":
        return "retrieve_authorized_knowledge"
    if next_action == "propose_action":
        return "propose_action"
    if next_action == "finish":
        return "finish"
    return "stop_safely"


def retrieve_authorized_knowledge_node(state: AgentState) -> dict[str, Any]:
    """Independent RAG boundary re-check (Part 6): retrieves candidates via
    Module 2's real rag_module.retrieve_documents(), then re-filters them
    through security_boundary.filter_authorized_chunks() -- which
    re-derives rag.is_authorized() itself -- before anything reaches
    retrieved_documents (and therefore the planner's context). This is
    enforced the same way regardless of whatever state Module 2's own
    retrieve_documents()/build_rag_context() currently happen to be in."""
    query = state.get("pending_query") or ""
    candidates = rag_module.retrieve_documents(query, state["requester_role"])
    authorized = sb.filter_authorized_chunks(candidates, state["requester_role"])
    blocked = len(candidates) - len(authorized)

    new_docs = [
        {
            "title": item.chunk.title,
            "source": item.chunk.source,
            "classification": item.chunk.classification,
            "text": item.chunk.text,
            "similarity": item.similarity,
        }
        for item in authorized
    ]
    summary = f"Retrieved {len(authorized)} authorized document(s) for '{query}'."
    if blocked:
        summary += f" {blocked} additional candidate(s) were excluded -- not authorized for role '{state['requester_role']}'."

    return {
        "retrieved_documents": [*state.get("retrieved_documents", []), *new_docs],
        "pending_query": None,
        "execution_trace": [
            *state.get("execution_trace", []),
            _trace(state, "retrieve_authorized_knowledge", summary, {"blocked_candidates": blocked}),
        ],
    }


def propose_action_node(state: AgentState) -> dict[str, Any]:
    """Normalizes the planner's raw proposal into a fully-resolved
    ProposedAction -- in particular, resolving target_employee_id (which
    the planner's JSON never supplies directly) via real backend lookups,
    the same way mcp_server.py resolves it server-side. This node still
    makes no authorization decision; it only prepares the facts
    authorize_action_node needs to make one."""
    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    tool_name = action.get("tool_name", "")
    arguments = dict(action.get("arguments") or {})
    # The model never decides identity; discard anything it tried to set.
    arguments.pop("requester_id", None)
    arguments.pop("requester_role", None)

    target_employee_id = _resolve_target_employee_id(tool_name, arguments, state["requester_id"])
    resolved = ProposedAction(tool_name=tool_name, arguments=arguments, target_employee_id=target_employee_id)

    return {
        "proposed_action": resolved,
        "execution_trace": [
            *state.get("execution_trace", []),
            _trace(state, "propose_action", f"Proposal normalized: {tool_name} -> target {target_employee_id}.", {"tool_name": tool_name, "target_employee_id": target_employee_id}),
        ],
    }


def authorize_action_node(state: AgentState) -> dict[str, Any]:
    """Independent MCP boundary re-check PLUS goal-scope enforcement (Part
    6, Demo 5 Scenario A): re-derives tool_policy.authorize()'s answer
    itself via security_boundary.check_mcp_authorization(), and
    independently checks security_boundary.action_within_scope() against
    the scope validate_goal_node granted -- so a broad-sounding goal
    cannot be used to approve an action outside the requester's own
    records unless the requester's role was actually granted BROAD scope."""
    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    tool_name = action.get("tool_name", "")
    arguments = action.get("arguments") or {}
    target_employee_id = action.get("target_employee_id")
    requester_id, requester_role = state["requester_id"], state["requester_role"]

    if tool_name == tool_policy.UPDATE_TICKET_STATUS:
        ticket = backend.find_ticket(str(arguments.get("ticket_id") or ""))
        if ticket is None:
            mcp_check = sb.McpAuthorizationCheck(False, f"No ticket found with id '{arguments.get('ticket_id')}'.")
        else:
            mcp_check = sb.check_mcp_authorization(
                tool_name,
                requester_id,
                requester_role,
                employee_id=ticket["employee_id"],
                new_status=arguments.get("new_status"),
                current_status=ticket["status"],
            )
    else:
        employee_id_kwarg = target_employee_id or requester_id
        mcp_check = sb.check_mcp_authorization(tool_name, requester_id, requester_role, employee_id=employee_id_kwarg)

    scope = sb.GoalScope(state.get("goal_scope", "self"))
    scope_ok = sb.action_within_scope(scope, requester_id, target_employee_id)

    allowed = mcp_check.allowed and scope_ok
    if not mcp_check.allowed:
        reason = mcp_check.reason
    elif not scope_ok:
        reason = (
            f"Action target '{target_employee_id}' is outside the goal's authorized scope "
            f"('{scope.value}') for requester '{requester_id}'."
        )
    else:
        reason = mcp_check.reason

    result = {"allowed": allowed, "reason": reason, "mcp_allowed": mcp_check.allowed, "scope_ok": scope_ok}
    return {
        "authorization_result": result,
        "execution_trace": [
            *state.get("execution_trace", []),
            _trace(state, "authorize_action", f"Authorization {'GRANTED' if allowed else 'DENIED'}: {reason}", result),
        ],
    }


def _route_after_authorize(state: AgentState) -> str:
    auth = state.get("authorization_result") or {}
    return "assess_risk" if auth.get("allowed") else "reject_or_escalate"


def assess_risk_node(state: AgentState) -> dict[str, Any]:
    """Pure risk classification (security_boundary.classify_risk) plus the
    one rule that genuinely differs by agent mode (Demo 2 -- Excessive
    Agency): security_boundary.requires_approval()."""
    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    target_employee_id = action.get("target_employee_id")
    target_is_self = (target_employee_id or state["requester_id"]) == state["requester_id"]

    risk = sb.classify_risk(action.get("tool_name", ""), target_is_self)
    approval_required = sb.requires_approval(risk, state["agent_mode"])

    return {
        "risk_level": risk.value,
        "approval_required": approval_required,
        "execution_trace": [
            *state.get("execution_trace", []),
            _trace(
                state,
                "assess_risk",
                f"Risk classified as {risk.value}. Approval required: {approval_required} (agent_mode={state['agent_mode']}).",
                {"risk_level": risk.value, "approval_required": approval_required},
            ),
        ],
    }


def request_approval_if_required_node(state: AgentState, config=None) -> dict[str, Any]:
    """Pauses the run (via interrupt()) when approval is required and no
    decision exists yet; otherwise proceeds. NEVER trusts the raw resume
    value as the decision -- always re-reads the authoritative record from
    approval_store after interrupt() returns control, and re-validates it
    against the EXACT action about to execute via
    approval_store.revalidate_for_resume() (the TOCTOU guard)."""
    if not state.get("approval_required"):
        return {
            "approval_state": "not_required",
            "execution_trace": [*state.get("execution_trace", []), _trace(state, "request_approval_if_required", "No approval required for this action.")],
        }

    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    tool_name = action.get("tool_name", "")
    arguments = action.get("arguments") or {}
    target_employee_id = action.get("target_employee_id")
    step_id = state.get("step_count", 0)

    fingerprint = sb.action_fingerprint(tool_name, arguments, target_employee_id)
    request = approval_store.create_request(
        run_id=state["run_id"],
        step_id=step_id,
        requester_id=state["requester_id"],
        requester_role=state["requester_role"],
        tool_name=tool_name,
        arguments=arguments,
        target_resource=target_employee_id,
        risk_level=state.get("risk_level") or sb.RiskLevel.MEDIUM.value,
        action_fingerprint=fingerprint,
    )

    current = approval_store.get(request.approval_id)
    if current is not None and current.status == approval_store.PENDING:
        # Pauses HERE on first entry. On resume, this entire node function
        # re-runs from the top -- create_request() above is idempotent, so
        # it returns this same record rather than a duplicate -- and by
        # then a human has already called approval_store.decide_approval()
        # via the Streamlit UI, so current.status will no longer be PENDING
        # on the re-run, and this branch will be skipped.
        interrupt(
            {
                "approval_id": request.approval_id,
                "run_id": state["run_id"],
                "tool_name": tool_name,
                "arguments": arguments,
                "target_employee_id": target_employee_id,
                "risk_level": state.get("risk_level"),
            }
        )
        current = approval_store.get(request.approval_id)

    if current is None:
        return {
            "approval_state": "denied",
            "approval_id": request.approval_id,
            "execution_trace": [*state.get("execution_trace", []), _trace(state, "request_approval_if_required", "Approval record vanished unexpectedly; treating as denied.")],
        }

    if current.status == approval_store.APPROVED:
        ok, reason = approval_store.revalidate_for_resume(current.approval_id, tool_name, arguments, target_employee_id)
        state_value = "approved" if ok else "denied"
        summary = "Approval validated for this exact action." if ok else f"Approval invalidated on resume: {reason}"
    elif current.status == approval_store.DENIED:
        state_value, summary = "denied", "Approval was denied by an authorized approver."
    else:
        state_value, summary = "pending", "Approval is still pending (unexpected state after interrupt resume)."

    return {
        "approval_state": state_value,
        "approval_id": current.approval_id,
        "execution_trace": [*state.get("execution_trace", []), _trace(state, "request_approval_if_required", summary, {"approval_id": current.approval_id, "status": current.status})],
    }


def _route_after_approval(state: AgentState) -> str:
    approval_state = state.get("approval_state")
    return "execute_tool" if approval_state in ("approved", "not_required") else "reject_or_escalate"


def execute_tool_node(state: AgentState) -> dict[str, Any]:
    """The only node that actually calls the real MCP server
    (mcp_client.call_tool()), and only after authorize_action_node and (if
    required) request_approval_if_required_node have both already said
    yes. Marks the approval consumed exactly once (approval_store.mark_
    executed) so a resumed/rerun path can never execute the same approved
    write twice, and stores the tool's own result into memory as
    UNTRUSTED TOOL_OBSERVATION content -- this is the planted-instruction
    vector Demo 4 (Agent Memory Poisoning) exercises."""
    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    tool_name = action.get("tool_name", "")
    arguments = action.get("arguments") or {}
    target_employee_id = action.get("target_employee_id")

    record = mcp_client.call_tool(tool_name, state["requester_id"], state["requester_role"], arguments)
    tool_call_count = state.get("tool_call_count", 0) + 1

    if state.get("approval_id") and state.get("approval_state") == "approved":
        try:
            approval_store.mark_executed(state["approval_id"])
        except ValueError:
            pass  # already marked (e.g. a retried resume) -- not a new execution

    observation = {
        "tool_name": tool_name,
        "arguments_sent": record.arguments_sent,
        "executed": record.executed,
        "is_error": record.is_error,
        "result": record.result,
    }
    retry_count = state.get("retry_count", 0)
    if record.is_error or not record.executed:
        retry_count += 1

    entry = mem.store(
        state["run_id"],
        state["requester_id"],
        mem.MemorySource.TOOL_OBSERVATION.value,
        json.dumps(record.result, default=str) if record.result is not None else (record.policy_reason or ""),
        state["agent_mode"],
    )

    base_summary = record.policy_reason or ("Executed successfully." if record.executed else "Tool call did not execute.")
    synopsis = _safe_result_synopsis(tool_name, record.result) if record.executed and not record.is_error else ""
    completed_or_denied_summary = {
        "tool_name": tool_name,
        "target_employee_id": target_employee_id,
        "summary": f"{base_summary} {synopsis}".strip() if synopsis else base_summary,
    }
    completed_actions = state.get("completed_actions", [])
    if record.executed:
        completed_actions = [*completed_actions, completed_or_denied_summary]

    return {
        "tool_call_count": tool_call_count,
        "retry_count": retry_count,
        "tool_observations": [*state.get("tool_observations", []), observation],
        "completed_actions": completed_actions,
        "memory_refs": [*state.get("memory_refs", []), entry.memory_id],
        "execution_trace": [
            *state.get("execution_trace", []),
            _trace(state, "execute_tool", f"Executed '{tool_name}': executed={record.executed}, is_error={record.is_error}.", observation),
        ],
    }


def observe_result_node(state: AgentState) -> dict[str, Any]:
    """Thin, explicit observation step (matches the spec's own "execute /
    observe" node pair) -- surfaces the most recent tool_observations entry
    into the trace as its own distinct, Inspector-visible event before
    control loops back to planning."""
    observations = state.get("tool_observations") or []
    last = observations[-1] if observations else None
    summary = f"Observed result of '{last['tool_name']}'." if last else "No tool observation to report."
    return {"execution_trace": [*state.get("execution_trace", []), _trace(state, "observe_result", summary, last or {})]}


def reject_or_escalate_node(state: AgentState) -> dict[str, Any]:
    """Records a denial (authorization-denied OR approval-denied) as a
    first-class, Inspector-visible outcome -- never silently dropped --
    and lets the planner's NEXT turn decide whether to mention it as
    something to escalate, per prompts.PLANNER_SYSTEM_PROMPT's explicit
    instruction not to repeat the exact same denied proposal."""
    action = state["proposed_action"] or ProposedAction(tool_name="", arguments={}, target_employee_id=None)
    auth = state.get("authorization_result") or {}
    approval_state = state.get("approval_state")

    if not auth.get("allowed", True):
        reason = auth.get("reason", "Not authorized.")
    elif approval_state == "denied":
        reason = "A required human approval was denied."
    else:
        reason = "Action could not proceed."

    denied_entry = {
        "tool_name": action.get("tool_name", ""),
        "target_employee_id": action.get("target_employee_id"),
        "reason": reason,
    }
    return {
        "denied_actions": [*state.get("denied_actions", []), denied_entry],
        "execution_trace": [*state.get("execution_trace", []), _trace(state, "reject_or_escalate", f"Denied: {reason}", denied_entry)],
    }


def finish_node(state: AgentState) -> dict[str, Any]:
    summary = state.get("final_result") or "(Run finished with no summary.)"
    system_entry = mem.store(state["run_id"], state["requester_id"], mem.MemorySource.SYSTEM.value, summary, state["agent_mode"])
    return {
        "status": "completed",
        "final_result": summary,
        "memory_refs": [*state.get("memory_refs", []), system_entry.memory_id],
        "execution_trace": [*state.get("execution_trace", []), _trace(state, "finish", "Run completed.")],
    }


def stop_safely_node(state: AgentState) -> dict[str, Any]:
    reason = state.get("stop_reason") or "Run stopped (goal rejected or execution ceiling reached)."
    return {
        "status": "stopped",
        "final_result": reason,
        "execution_trace": [*state.get("execution_trace", []), _trace(state, "stop_safely", reason)],
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

# LangGraph's own generic recursion guard (default 25 "super-steps") is a
# framework-level safety net, not this app's security control -- that
# control is security_boundary.budget_exceeded(), checked deterministically
# inside plan_next_step_node on every single planning turn. But one
# AgentState "step" (one planner call) can take ~7 real graph-node hops
# when it results in a tool call (propose_action -> authorize_action ->
# assess_risk -> request_approval_if_required -> execute_tool ->
# observe_result -> plan_next_step), so the framework guard must be sized
# comfortably above this app's own absolute ceiling
# (security_boundary.ABSOLUTE_MAX_STEPS) or it would fire first and mask
# which control actually stopped the run. Sized generously, not
# unlimited -- it is still a hard ceiling, just one high enough that
# budget_exceeded() is always what a student observes stopping a runaway
# demonstration, exactly as Part 10 Demo 5 Scenario B requires.
_GRAPH_RECURSION_LIMIT = 300

_compiled_graph = None


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("validate_goal", validate_goal_node)
    graph.add_node("plan_next_step", plan_next_step_node)
    graph.add_node("retrieve_authorized_knowledge", retrieve_authorized_knowledge_node)
    graph.add_node("propose_action", propose_action_node)
    graph.add_node("authorize_action", authorize_action_node)
    graph.add_node("assess_risk", assess_risk_node)
    graph.add_node("request_approval_if_required", request_approval_if_required_node)
    graph.add_node("execute_tool", execute_tool_node)
    graph.add_node("observe_result", observe_result_node)
    graph.add_node("reject_or_escalate", reject_or_escalate_node)
    graph.add_node("finish", finish_node)
    graph.add_node("stop_safely", stop_safely_node)

    graph.add_edge(START, "validate_goal")
    graph.add_conditional_edges("validate_goal", _route_after_validate, ["plan_next_step", "stop_safely"])
    graph.add_conditional_edges(
        "plan_next_step",
        _route_after_plan,
        ["plan_next_step", "retrieve_authorized_knowledge", "propose_action", "finish", "stop_safely"],
    )
    graph.add_edge("retrieve_authorized_knowledge", "plan_next_step")
    graph.add_edge("propose_action", "authorize_action")
    graph.add_conditional_edges("authorize_action", _route_after_authorize, ["assess_risk", "reject_or_escalate"])
    graph.add_edge("assess_risk", "request_approval_if_required")
    graph.add_conditional_edges("request_approval_if_required", _route_after_approval, ["execute_tool", "reject_or_escalate"])
    graph.add_edge("execute_tool", "observe_result")
    graph.add_edge("observe_result", "plan_next_step")
    graph.add_edge("reject_or_escalate", "plan_next_step")
    graph.add_edge("finish", END)
    graph.add_edge("stop_safely", END)

    return graph.compile(checkpointer=InMemorySaver())


def get_graph():
    """Cached singleton compiled graph. The InMemorySaver checkpointer is
    local-process, in-memory state keyed by thread_id (== run_id) -- no
    cloud tracing, no external DB, per Part 17."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def get_interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extracts the human-readable payload passed to interrupt() from a
    graph.invoke()/.get_state() result, or None if the run did not pause.
    Used by module.py's UI to render the approval prompt."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    if isinstance(first, Interrupt):
        return first.value
    return first


def run_agent(
    run_id: str,
    requester_id: str,
    requester_role: str,
    agent_mode: str,
    goal: str,
    provider: Provider,
) -> dict[str, Any]:
    """Start a brand-new run. requester_id/requester_role must already be
    the trusted application session's identity (app.core.security.User) --
    this function never reads identity from goal text or anywhere else."""
    graph = get_graph()
    initial_state = new_state(run_id, requester_id, requester_role, agent_mode, goal)
    config = {"configurable": {"thread_id": run_id, "provider": provider}, "recursion_limit": _GRAPH_RECURSION_LIMIT}
    return graph.invoke(initial_state, config=config)


def resume_agent(run_id: str, provider: Provider, resume_value: Any = True) -> dict[str, Any]:
    """Resume a paused run. resume_value is deliberately unused for any
    security decision -- see request_approval_if_required_node's docstring
    -- it only needs to be *some* value so Command(resume=...) can hand
    control back past the interrupt() call that is currently paused."""
    graph = get_graph()
    config = {"configurable": {"thread_id": run_id, "provider": provider}, "recursion_limit": _GRAPH_RECURSION_LIMIT}
    return graph.invoke(Command(resume=resume_value), config=config)


def get_run_state(run_id: str) -> dict[str, Any] | None:
    """Read back the current checkpointed state for a run without
    advancing it -- used by the UI to redraw the Agent Inspector after a
    Streamlit rerun without resuming anything."""
    graph = get_graph()
    config = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(config)
    return dict(snapshot.values) if snapshot and snapshot.values else None
