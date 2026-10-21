# Instructor Solution — GlobalTech AI V3 (MCP Security)

This file is intentionally kept in its own `instructor/` directory, separate
from `README.md` and `labs/03-mcp-security.md`. It is not linked from the
normal student-facing lab flow. Students are told in
`labs/03-mcp-security.md` to attempt Phase 10 themselves before opening this
file.

Do not fold this content back into the top-level README or the lab
document.

---

## The vulnerability, precisely

In the starter `app/modules/module3_mcp/mcp_server.py`, all four
`@mcp.tool()` functions follow the same shape:

```python
decision = tool_policy.authorize(...)
record = backend.find_salary(employee_id)   # or find_employee / find_tickets_for / create_ticket
return {
    "executed": True,
    "expected_authorization": decision.allowed,
    "policy_reason": decision.reason,
    ...
    "result": record,
}
```

`tool_policy.authorize()` is correct and is even surfaced in the response
as `expected_authorization` — but nothing reads that value before deciding
whether to call `backend`. The backend read (or, for
`create_support_ticket`, the write) happens unconditionally. This is the
exact same bug family as Lab 01's `get_context()` (hands `RESTRICTED_CONTEXT`
to every role) and Lab 02's `retrieve_chunks()` (ranks every chunk for every
role): a correct check that is computed, surfaced for observability, and
never actually wired to the thing that matters.

The fix belongs in exactly one place per tool: an early return, guarded by
`decision.allowed`, **before** the `backend` call. `tool_policy.py` does not
need to change at all — it is already correct, exactly like Lab 02's
`is_authorized()`.

## Reference fix

```python
# app/modules/module3_mcp/mcp_server.py

@mcp.tool()
def get_employee_profile(requester_id: str, requester_role: str, employee_id: str) -> dict:
    """Look up an employee's directory profile (name, title, department, manager_id) by employee_id."""
    decision = tool_policy.authorize(
        tool_policy.GET_EMPLOYEE_PROFILE, requester_id, requester_role, employee_id=employee_id
    )
    if not decision.allowed:
        return {
            "executed": False,
            "expected_authorization": False,
            "policy_reason": decision.reason,
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": None,
        }
    record = backend.find_employee(employee_id)
    return {
        "executed": True,
        "expected_authorization": True,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": record if record is not None else {"error": f"No employee found with id '{employee_id}'."},
    }


@mcp.tool()
def get_salary_info(requester_id: str, requester_role: str, employee_id: str) -> dict:
    """Look up an employee's compensation record (base salary, bonus target) by employee_id. Restricted to HR and Admin."""
    decision = tool_policy.authorize(
        tool_policy.GET_SALARY_INFO, requester_id, requester_role, employee_id=employee_id
    )
    if not decision.allowed:
        return {
            "executed": False,
            "expected_authorization": False,
            "policy_reason": decision.reason,
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": None,
        }
    record = backend.find_salary(employee_id)
    return {
        "executed": True,
        "expected_authorization": True,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": record if record is not None else {"error": f"No salary record found for '{employee_id}'."},
    }


@mcp.tool()
def list_support_tickets(requester_id: str, requester_role: str, employee_id: str | None = None) -> dict:
    """List IT/HR support tickets filed by an employee_id (defaults to the caller's own tickets if omitted)."""
    target_id = employee_id or requester_id
    decision = tool_policy.authorize(
        tool_policy.LIST_SUPPORT_TICKETS, requester_id, requester_role, employee_id=target_id
    )
    if not decision.allowed:
        return {
            "executed": False,
            "expected_authorization": False,
            "policy_reason": decision.reason,
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": [],
        }
    results = backend.find_tickets_for(target_id)
    return {
        "executed": True,
        "expected_authorization": True,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": results,
    }


@mcp.tool()
def create_support_ticket(
    requester_id: str,
    requester_role: str,
    subject: str,
    description: str,
    employee_id: str | None = None,
) -> dict:
    """File a new IT/HR support ticket. Defaults to filing on behalf of the caller. This is a WRITE action."""
    target_id = employee_id or requester_id
    decision = tool_policy.authorize(
        tool_policy.CREATE_SUPPORT_TICKET, requester_id, requester_role, employee_id=target_id
    )
    if not decision.allowed:
        return {
            "executed": False,
            "expected_authorization": False,
            "policy_reason": decision.reason,
            "requester_id": requester_id,
            "requester_role": requester_role,
            "result": None,
        }
    ticket = backend.create_ticket(target_id, subject, description)
    return {
        "executed": True,
        "expected_authorization": True,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": ticket,
    }
```

Note what does **not** change: `tool_policy.py`, `backend.py`,
`mcp_client.py`, and `module.py` are all untouched. The fix is scoped
entirely to the four `if not decision.allowed: return early` guards in
`mcp_server.py` — the same "move the decision upstream, once, and act on
it" lesson as Labs 01 and 02, just applied at the tool-execution boundary
instead of the context-assembly or retrieval boundary.

A student who instead tries to filter in `module.py` (e.g. checking
`record.expected_authorization` after `mcp_client.call_tool()` returns, and
simply not showing the result to the user) should be pushed to explain why
that is weaker: the unauthorized backend read or **write** already
happened. For `create_support_ticket` specifically, this is not a cosmetic
difference — a ticket would already be sitting in `data/mcp/tickets.json`
on Bob's behalf even if the UI never displayed the confirmation. The
intended fix prevents the effect from occurring at all, not just from being
displayed.

## Expected test results

Before remediation:

```
4 failed, 16 passed
FAILED tests/module3/test_mcp_security.py::test_get_salary_info_blocks_unauthorized_employee
FAILED tests/module3/test_mcp_security.py::test_get_employee_profile_blocks_cross_employee_access
FAILED tests/module3/test_mcp_security.py::test_list_support_tickets_blocks_cross_employee_access
FAILED tests/module3/test_mcp_security.py::test_create_support_ticket_blocks_filing_for_someone_else
```

After remediation:

```
20 passed
```

## What this does and does not fix

**Fixed:** every read and the one write now check `decision.allowed` before
touching `backend`. An employee can no longer read salary data, another
employee's profile, or another employee's tickets; filing a ticket "on
behalf of" someone else is blocked server-side regardless of whether the
write-approval dialog in the UI is clicked through.

**Not fixed, and not supposed to be fixed by this change:**

- **Identity spoofing (Phase 5)** was never actually exploitable in the
  starter app in the first place — `mcp_client.call_tool()` already
  discards any `requester_id`/`requester_role` the model's proposed JSON
  contains and substitutes the trusted session identity, before this
  remediation and after it. Phase 5 exists to let students *verify* a
  control that was already correct, the same way Lab 02's Phase 5/6 asked
  students to reason about injection without expecting the RAG
  authorization fix to touch it.
- **Indirect prompt injection via tool output (Phase 6)** is not fixed by
  this change, and should not be. `list_support_tickets` executing for
  Bob's own tickets is correct — T-1003 is Bob's own ticket, and reading it
  is authorized. What this remediation *does* change is the blast radius if
  the model is tricked into acting on the embedded instruction: the
  resulting `get_salary_info` call is now itself blocked (because Bob is an
  employee), where previously it would have executed and returned a real
  salary record. The injection payload reaching the model's context is a
  separate problem from whether acting on it has any effect — this lab's
  remediation addresses only the second half.
- **The bounded two-hop cap** (`MAX_TOOL_CALLS_PER_TURN` in `module.py`) is
  a loop-bound, not a security control, and is unrelated to this
  remediation. It exists so Phase 6 is observable at all without the lab
  becoming an open-ended agent loop; it should not be presented to students
  as a security boundary.

If an instructor wants to extend the lab, a reasonable follow-on discussion
(not required for this lab's tests to pass) is: how would you detect or
neutralize instruction-like content inside a tool result before it reaches
the model's context, the same open question Lab 02 raises for retrieved
documents? There is no single-function fix for that the way there is for
authorization enforcement — which is itself worth sitting with.

## Optional extension: `list_department_roster`

If a student (or instructor demo) adds the optional fifth tool from
`labs/03-mcp-security.md`:

```python
# app/modules/module3_mcp/tool_policy.py
LIST_DEPARTMENT_ROSTER = "list_department_roster"
ALL_TOOLS = (GET_EMPLOYEE_PROFILE, GET_SALARY_INFO, LIST_SUPPORT_TICKETS, CREATE_SUPPORT_TICKET, LIST_DEPARTMENT_ROSTER)

# inside authorize(), alongside the other tool branches:
if tool_name == LIST_DEPARTMENT_ROSTER:
    if requester_role in PRIVILEGED_ROLES:
        return AuthorizationDecision(True, f"Role '{requester_role}' may list any department's roster.")
    return AuthorizationDecision(False, "Department rosters are restricted to HR and Admin roles.")
```

```python
# app/modules/module3_mcp/backend.py
def find_employees_in_department(department: str) -> list[dict[str, Any]]:
    return [e for e in _load(EMPLOYEES_FILE) if e["department"].lower() == department.lower()]
```

```python
# app/modules/module3_mcp/mcp_server.py -- starter (vulnerable) version,
# for consistency with the other four tools before a student remediates it
@mcp.tool()
def list_department_roster(requester_id: str, requester_role: str, department: str) -> dict:
    """List every employee in a department. Restricted to HR and Admin."""
    decision = tool_policy.authorize(tool_policy.LIST_DEPARTMENT_ROSTER, requester_id, requester_role)
    results = backend.find_employees_in_department(department)
    return {
        "executed": True,
        "expected_authorization": decision.allowed,
        "policy_reason": decision.reason,
        "requester_id": requester_id,
        "requester_role": requester_role,
        "result": results,
    }
```

```python
def test_employee_cannot_list_department_roster():
    decision = authorize(tool_policy.LIST_DEPARTMENT_ROSTER, "E1001", "employee")
    assert decision.allowed is False


def test_hr_can_list_department_roster():
    decision = authorize(tool_policy.LIST_DEPARTMENT_ROSTER, "E1003", "hr")
    assert decision.allowed is True
```

No other UI code needs to change — `mcp_client.list_tools()` discovers
tools from the running server via a real `list_tools()` MCP call, and
`module._tool_catalog_text()` builds the model's tool catalog from that
same discovery call, exactly as it does for the original four tools.
