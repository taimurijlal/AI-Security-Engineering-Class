"""
Local, application-side human-approval service for Module 4 (Agentic AI
Security).

Part 5 of the lab spec explicitly permits "an application-side approval
service... used instead of an MCP tool if more appropriate" for the
approval-request capability, since approval is a property of the *agent*
boundary (should this autonomous run be allowed to act right now, without
further human sign-off), not of the MCP server's own authorization
boundary (is this action ever permitted at all). Keeping it here, as a
plain flat-file store exactly like app/modules/module3_mcp/backend.py,
avoids conflating those two boundaries and avoids adding an MCP tool whose
only caller would be this same application anyway.

SECURITY PROPERTIES THIS FILE IS RESPONSIBLE FOR (Part 9):
- Creating an approval request never grants it -- status starts "pending"
  and only decide_approval() can change that.
- An approval is bound to the exact action: tool name, arguments, and
  target resource are hashed into an `action_fingerprint`
  (security_boundary.action_fingerprint) at request time. revalidate()
  recomputes that hash before resume and refuses if anything drifted --
  this is the TOCTOU guard: a modified argument invalidates a stale
  approval instead of silently reusing it.
- An approval can be consumed (executed) at most once -- mark_executed()
  is idempotent-safe and execute-time code must check is_unconsumed()
  first, so a Streamlit rerun that re-enters the same code path cannot
  execute the same approved write twice.
- The agent must never approve its own action: decide_approval() requires
  the approving identity to differ from the requesting identity AND to
  hold a privileged role (hr/admin) -- see validate_approver().
- create_request() is idempotent per (run_id, step_id, action_fingerprint):
  LangGraph re-executes a node's entire function body from the top on
  every resume (confirmed by direct experimentation with this app's
  interrupt()/Command(resume=...) usage), so the node that requests
  approval runs its "create the request" line again on every single
  resume of the same paused run -- not just once. If that produced a
  fresh random ID each time, every resume would silently mint a brand
  new duplicate pending request instead of rejoining the one a human may
  already be looking at (or already decided). The approval_id is instead
  derived deterministically from exactly the facts that identify "this
  same approval ask" -- which run, which step, which exact action -- so
  replays of the same node call land on the same record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.modules.module3_mcp.tool_policy import PRIVILEGED_ROLES

STORE_FILE = Path(__file__).resolve().parents[3] / "data" / "module4_agent" / "approvals.json"

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"


@dataclass
class ApprovalRequest:
    approval_id: str
    run_id: str
    step_id: int
    requester_id: str
    requester_role: str
    tool_name: str
    arguments: dict[str, Any]
    target_resource: str | None
    risk_level: str
    action_fingerprint: str
    status: str = PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decided_by: str | None = None
    decided_at: str | None = None
    executed: bool = False


def _load() -> list[dict[str, Any]]:
    if not STORE_FILE.exists():
        return []
    with STORE_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(records: list[dict[str, Any]]) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STORE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)
        fh.write("\n")


def _deterministic_approval_id(run_id: str, step_id: int, action_fingerprint: str) -> str:
    """Derive a stable approval_id from exactly the facts that identify
    'this same approval ask': which run, which step within that run, and
    which exact action (already hashed into action_fingerprint). Calling
    create_request() again with the same three inputs -- which is exactly
    what happens every time LangGraph replays a node body on resume --
    must land on the same ID, not mint a new one."""
    canonical = f"{run_id}:{step_id}:{action_fingerprint}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def create_request(
    run_id: str,
    step_id: int,
    requester_id: str,
    requester_role: str,
    tool_name: str,
    arguments: dict[str, Any],
    target_resource: str | None,
    risk_level: str,
    action_fingerprint: str,
) -> ApprovalRequest:
    """Record a new pending approval request. DOES NOT APPROVE ANYTHING --
    status is always 'pending' on creation. Only decide_approval() below
    can change that.

    IDEMPOTENT by (run_id, step_id, action_fingerprint): if a request with
    the resulting deterministic ID already exists (because this exact call
    happened before -- e.g. a prior pass through this node, before a
    resume), that existing record is returned UNCHANGED instead of a
    duplicate being inserted. This is what makes it safe for the graph's
    approval node to call create_request() unconditionally every time its
    function body runs, including every time it re-runs from the top after
    a LangGraph resume."""
    approval_id = _deterministic_approval_id(run_id, step_id, action_fingerprint)
    records = _load()
    for record in records:
        if record["approval_id"] == approval_id:
            return ApprovalRequest(**record)
    request = ApprovalRequest(
        approval_id=approval_id,
        run_id=run_id,
        step_id=step_id,
        requester_id=requester_id,
        requester_role=requester_role,
        tool_name=tool_name,
        arguments=dict(arguments),
        target_resource=target_resource,
        risk_level=risk_level,
        action_fingerprint=action_fingerprint,
    )
    records.append(asdict(request))
    _save(records)
    return request


def get(approval_id: str) -> ApprovalRequest | None:
    for record in _load():
        if record["approval_id"] == approval_id:
            return ApprovalRequest(**record)
    return None


def list_for_run(run_id: str) -> list[ApprovalRequest]:
    return [ApprovalRequest(**r) for r in _load() if r["run_id"] == run_id]


def validate_approver(request: ApprovalRequest, approver_employee_id: str, approver_role: str) -> tuple[bool, str]:
    """The agent must never approve its own action: the approving identity
    must differ from the requesting identity and must hold a privileged
    role (hr/admin). This is the control a student verifies in Demo 2 and
    that Part 9 requires -- approval must come from "a trusted UI
    interaction by an authorized approver", never from the run's own
    requester."""
    if approver_employee_id == request.requester_id:
        return False, "An action's own requester may not approve it."
    if approver_role not in PRIVILEGED_ROLES:
        return False, f"Approving an action requires an elevated role (hr/admin); '{approver_role}' is not eligible."
    return True, "Approver is a distinct, privileged identity."


def decide_approval(approval_id: str, approver_employee_id: str, approver_role: str, approve: bool) -> ApprovalRequest:
    """Approve or deny a pending request. Raises ValueError if the request
    does not exist, is not pending, or the approver fails
    validate_approver() -- callers (the Streamlit approval UI) must check
    validate_approver() themselves first to render a clean error instead
    of relying on this exception for UX."""
    records = _load()
    for record in records:
        if record["approval_id"] == approval_id:
            request = ApprovalRequest(**record)
            if request.status != PENDING:
                raise ValueError(f"Approval {approval_id} is already '{request.status}'; it cannot be re-decided.")
            ok, reason = validate_approver(request, approver_employee_id, approver_role)
            if not ok:
                raise ValueError(reason)
            record["status"] = APPROVED if approve else DENIED
            record["decided_by"] = approver_employee_id
            record["decided_at"] = datetime.now(timezone.utc).isoformat()
            _save(records)
            return ApprovalRequest(**record)
    raise ValueError(f"No approval request found with id '{approval_id}'.")


def revalidate_for_resume(approval_id: str, tool_name: str, arguments: dict[str, Any], target_resource: str | None) -> tuple[bool, str]:
    """TOCTOU guard for resuming a paused graph run (Part 6, Part 9):
    recompute the action's fingerprint from what is about to be executed
    and compare it against what was approved. Any drift (arguments
    changed, different tool, different target) invalidates the approval --
    it must never be silently reused for a different action than the one a
    human actually looked at."""
    from app.modules.module4_agent.security_boundary import action_fingerprint

    request = get(approval_id)
    if request is None:
        return False, f"No approval request found with id '{approval_id}'."
    if request.status != APPROVED:
        return False, f"Approval status is '{request.status}', not approved."
    if request.executed:
        return False, "This approval has already been executed once; it cannot be executed again."
    current_fp = action_fingerprint(tool_name, arguments, target_resource)
    if current_fp != request.action_fingerprint:
        return False, "The proposed action changed after approval was granted; the approval no longer applies."
    return True, "Approval is valid and unconsumed for this exact action."


def mark_executed(approval_id: str) -> None:
    """Idempotency guard: call exactly once, immediately after a genuinely
    successful execution, so a Streamlit rerun that re-enters this code
    path cannot execute the same approved write a second time."""
    records = _load()
    for record in records:
        if record["approval_id"] == approval_id:
            record["executed"] = True
            _save(records)
            return
    raise ValueError(f"No approval request found with id '{approval_id}'.")


def clear_for_run(run_id: str) -> None:
    """Module 4's own reset -- removes only this run's approval requests,
    never touching any other run's or any other module's data."""
    remaining = [r for r in _load() if r["run_id"] != run_id]
    _save(remaining)


def clear_all() -> None:
    """Full reset of Module 4's approval store (used by the module's own
    'Reset Module 4' control) -- does not touch any other module's data,
    since this file lives entirely under data/module4_agent/."""
    _save([])
