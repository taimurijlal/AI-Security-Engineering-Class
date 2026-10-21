"""
Deterministic, LLM-independent security boundary for Module 4 (Agentic AI
Security).

THE CORE PRINCIPLE THIS FILE IMPLEMENTS: "The agent proposes. The
application authorizes. The trusted execution layer enforces." Nothing in
this file ever calls an LLM provider, reads Streamlit session state, or
trusts anything a model, a retrieved document, or a tool result says. It
is a pure-function policy layer, directly unit-testable, in exactly the
same spirit as app/modules/module3_mcp/tool_policy.py and
app/modules/module2_rag/rag.is_authorized() -- and it deliberately reuses
those two functions rather than re-implementing authorization logic.

WHY THIS IS A *THIRD*, INDEPENDENT BOUNDARY (read this before changing
anything): Module 2's retrieve_documents() and Module 3's mcp_server.py
are themselves lab exercises -- their own enforcement may currently be in
the deliberately vulnerable starter state, depending on what a student has
fixed so far in those modules. Module 4 must be able to demonstrate
agent-layer vulnerabilities (excessive agency, missing approval, goal
manipulation, memory poisoning) without ever disabling, bypassing, or
depending on the *current* state of Module 2/3's own enforcement -- the
spec for this lab is explicit: "Never disable RAG or MCP authorization to
make an agent demonstration appear successful." So this module re-derives
the correct authorization decision itself, straight from the same pure
policy functions Module 2 and Module 3 already expose
(rag.is_authorized(), tool_policy.authorize()), and enforces that decision
before any RAG content enters the agent's context or any MCP call is
actually sent -- regardless of whether module2_rag/rag.py's retrieval
filtering or module3_mcp/mcp_server.py's own gating happen to be fixed yet.

Everything that genuinely differs between Module 4's "vulnerable" and
"secured" agent modes lives in this file: goal scoping, approval
requirement, and execution budgets. RAG/MCP authorization re-checks below
are NOT mode-dependent -- they are always enforced, in both modes, per the
lab's non-negotiable design rule #3 and Part 11's explicit comparison
table.
"""

from __future__ import annotations

import hashlib
import json as _json
from dataclasses import dataclass
from enum import Enum

from app.modules.module2_rag import rag as rag_module
from app.modules.module3_mcp import tool_policy

AGENT_MODE_VULNERABLE = "vulnerable"
AGENT_MODE_SECURED = "secured"

# ---------------------------------------------------------------------------
# Goal validation and scoping (Demo 5, Scenario A)
# ---------------------------------------------------------------------------


class GoalScope(str, Enum):
    SELF = "self"  # the agent may only act on the authenticated user's own records
    BROAD = "broad"  # explicitly, deliberately granted cross-employee scope (privileged roles only)


@dataclass(frozen=True)
class GoalValidation:
    allowed: bool
    scope: GoalScope
    reason: str


_BROAD_GOAL_MARKERS = (
    "all employee",
    "all outstanding",
    "everyone",
    "every request",
    "all tickets",
    "all requests",
    "every employee",
)


def validate_goal(goal: str, requester_role: str) -> GoalValidation:
    """Decide whether a stated natural-language goal may proceed, and at
    what scope.

    DELIBERATE DESIGN (this is what Demo 5 Scenario A tests): a
    broad-sounding goal such as "resolve all outstanding employee
    requests" is never rejected outright -- a real assistant should be
    able to triage a queue -- but for a standard employee it is
    deterministically constrained to GoalScope.SELF regardless of
    phrasing. The LLM may still *propose* an action against another
    employee's ticket while "being helpful" about a broad-sounding goal;
    this function is what the agent graph's authorize_action node actually
    consults before any such proposal can execute, and it cannot be argued
    into BROAD scope for a non-privileged role by anything in the goal
    text, a retrieved document, or a tool result.
    """
    text = (goal or "").strip().lower()
    if not text:
        return GoalValidation(False, GoalScope.SELF, "Empty goal.")

    if requester_role in tool_policy.PRIVILEGED_ROLES:
        if any(marker in text for marker in _BROAD_GOAL_MARKERS):
            return GoalValidation(
                True, GoalScope.BROAD, f"Role '{requester_role}' may act across employees for broad goals."
            )
        return GoalValidation(True, GoalScope.SELF, f"Role '{requester_role}' goal accepted at self scope.")

    return GoalValidation(
        True,
        GoalScope.SELF,
        "Standard employee goals are always scoped to the requester's own records, regardless of phrasing.",
    )


def action_within_scope(scope: GoalScope, requester_id: str, target_employee_id: str | None) -> bool:
    """Deterministically checks whether a *specific proposed action's*
    target falls inside the scope validate_goal() granted. Called again,
    independently, at authorize_action time -- not just once at goal
    validation time -- so a broad goal cannot be used to launder a single
    out-of-scope action through an initially-accepted goal."""
    if scope == GoalScope.BROAD:
        return True
    return target_employee_id is None or target_employee_id == requester_id


# ---------------------------------------------------------------------------
# Independent RAG boundary re-check (always enforced, both agent modes)
# ---------------------------------------------------------------------------


def filter_authorized_chunks(retrieved: list, requester_role: str) -> list:
    """Agent-layer re-derivation of rag.is_authorized() -- filters OUT any
    chunk the requester's role is not authorized for, independent of
    whatever module2_rag/rag.py's retrieve_documents()/build_rag_context()
    currently do. `retrieved` is a list of
    app.modules.module2_rag.rag.RetrievedChunk. Always enforced in both
    Module 4 agent modes; this is the RAG boundary from Part 6."""
    return [item for item in retrieved if rag_module.is_authorized(requester_role, item.chunk.allowed_roles)]


# ---------------------------------------------------------------------------
# Independent MCP boundary re-check (always enforced, both agent modes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpAuthorizationCheck:
    allowed: bool
    reason: str


def check_mcp_authorization(tool_name: str, requester_id: str, requester_role: str, **kwargs: object) -> McpAuthorizationCheck:
    """Agent-layer re-derivation of tool_policy.authorize() -- evaluated
    independently by the agent graph's authorize_action node *before* any
    MCP call is sent, regardless of what mcp_server.py's own gating
    currently does. Always enforced in both Module 4 agent modes; this is
    the MCP boundary from Part 6. An unrecognized tool is denied outright
    -- the agent can never invent a tool outside the registered MCP
    interface."""
    if tool_name not in tool_policy.ALL_TOOLS:
        return McpAuthorizationCheck(False, f"'{tool_name}' is not a registered MCP tool.")
    decision = tool_policy.authorize(tool_name, requester_id, requester_role, **kwargs)
    return McpAuthorizationCheck(decision.allowed, decision.reason)


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def classify_risk(tool_name: str, target_is_self: bool) -> RiskLevel:
    """Pure classification: any read-only tool is LOW risk. A write action
    on the requester's own record is MEDIUM. A write action targeting
    someone else's record is HIGH -- these are exactly the write tools
    registered in tool_policy.WRITE_TOOLS (create_support_ticket,
    update_ticket_status)."""
    if tool_name not in tool_policy.WRITE_TOOLS:
        return RiskLevel.LOW
    return RiskLevel.MEDIUM if target_is_self else RiskLevel.HIGH


# ---------------------------------------------------------------------------
# Approval requirement (Demo 2 -- the one rule that differs by agent mode)
# ---------------------------------------------------------------------------


def requires_approval(risk: RiskLevel, agent_mode: str) -> bool:
    """DELIBERATE VULNERABILITY (Demo 2 -- Excessive Agency): in
    AGENT_MODE_VULNERABLE, human approval is never required, even for a
    write action that IS correctly authorized at the MCP boundary -- MCP
    authorization answers "is this action permitted at all", not "may the
    agent carry it out autonomously, right now, without a human in the
    loop." Those are different questions; collapsing them is exactly the
    excessive-agency bug this demo exists to teach. In
    AGENT_MODE_SECURED, any MEDIUM or HIGH risk (i.e. any write action)
    pauses for human approval before execution, regardless of MCP
    authorization -- "an action that passes MCP authorization may still
    require agent-level approval" (Part 6)."""
    if agent_mode == AGENT_MODE_VULNERABLE:
        return False
    return risk in (RiskLevel.MEDIUM, RiskLevel.HIGH)


# ---------------------------------------------------------------------------
# Approval binding -- prevents replay, drift, and cross-run reuse (Part 9)
# ---------------------------------------------------------------------------


def action_fingerprint(tool_name: str, arguments: dict, target_resource: str | None) -> str:
    """A stable hash binding an approval to the exact tool + arguments +
    target. Computed once when approval is requested, and recomputed again
    on resume; a mismatch means the proposed action drifted after approval
    was granted (modified arguments, a different target, or a replayed
    stale approval) and the approval must be treated as invalid -- see
    Part 9's "an approval must be bound to the exact action/arguments/
    target/run" requirement and the TOCTOU-safe-resume requirement in
    Part 6."""
    canonical = _json.dumps(
        {"tool": tool_name, "arguments": arguments, "target": target_resource}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Execution budgets (Demo 5, Scenario B)
# ---------------------------------------------------------------------------

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOOL_CALLS = 6
DEFAULT_MAX_RETRIES_PER_TOOL = 2

# Even the vulnerable agent mode must have an absolute safety ceiling --
# Part 10 Demo 5 Scenario B and Part 17 are explicit that this is true
# "even in vulnerable mode." This is not the security control being taught
# (that's requires_approval()'s mode difference); it is a non-negotiable
# backstop so a runaway demonstration can never hang the lab or the
# process.
ABSOLUTE_MAX_STEPS = 20
ABSOLUTE_MAX_TOOL_CALLS = 10
ABSOLUTE_MAX_RETRIES_PER_TOOL = 6


@dataclass(frozen=True)
class BudgetLimits:
    max_steps: int
    max_tool_calls: int
    max_retries_per_tool: int


def budget_limits(agent_mode: str) -> BudgetLimits:
    """DELIBERATE: vulnerable mode is given a much *larger* -- but still
    finite -- budget than secured mode, so a repeatedly-failing tool's
    excessive-retry behavior (Demo 5 Scenario B) is actually observable
    before the run stops, while secured mode stops quickly. Vulnerable
    mode is never unlimited."""
    if agent_mode == AGENT_MODE_VULNERABLE:
        return BudgetLimits(
            max_steps=ABSOLUTE_MAX_STEPS,
            max_tool_calls=ABSOLUTE_MAX_TOOL_CALLS,
            max_retries_per_tool=ABSOLUTE_MAX_RETRIES_PER_TOOL,
        )
    return BudgetLimits(
        max_steps=DEFAULT_MAX_STEPS,
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        max_retries_per_tool=DEFAULT_MAX_RETRIES_PER_TOOL,
    )


def budget_exceeded(step_count: int, tool_call_count: int, retry_count: int, limits: BudgetLimits) -> str | None:
    """Returns a human-readable stop_reason if any execution ceiling has
    been reached, else None. Checked by the graph's plan_next_step node on
    every iteration -- this is the real, enforced stop condition, not a
    suggestion the LLM can decide to ignore."""
    if step_count >= limits.max_steps:
        return f"Step budget exceeded ({step_count}/{limits.max_steps})."
    if tool_call_count >= limits.max_tool_calls:
        return f"Tool-call budget exceeded ({tool_call_count}/{limits.max_tool_calls})."
    if retry_count >= limits.max_retries_per_tool:
        return f"Retry budget exceeded after repeated tool failures ({retry_count}/{limits.max_retries_per_tool})."
    return None
