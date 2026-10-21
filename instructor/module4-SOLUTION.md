# Instructor Solution — GlobalTech AI V4 (Agentic AI Security)

This file is intentionally kept in its own `instructor/` directory, separate
from `README.md` and any student-facing lab document, following the same
convention as `module1-SOLUTION.md`, `module2-SOLUTION.md`, and
`module3-SOLUTION.md`.

**Why this document does not follow the Modules 1–3 format.** Each of those
three modules plants exactly one authorization bug in one starter file, and
the instructor doc's job is to show the single fix and the before/after test
delta. Module 4 has no equivalent single bug to find: it ships with a
genuine dual **Agent mode** architecture (`vulnerable` vs `secured`, see
`app/modules/module4_agent/security_boundary.py::requires_approval()`) where
both the vulnerable and secured code paths are intentional, finished,
simultaneously-present behaviors selected at runtime — a student (or an
instructor recording a demo) switches between them with a radio button, not
by writing a fix. So instead of "the vulnerability, precisely" + "reference
fix," this document follows the 14-item structure Part 21 of the lab spec
requires.

---

## 1. Summary of the implementation

Module 4 ("Agent Security") adds a real LangGraph `StateGraph` agent to the
existing GlobalTech AI Streamlit application, as a fourth sidebar module
alongside Module 1 (Prompt Security), Module 2 (RAG Security), and Module 3
(MCP Security). The agent accepts a natural-language goal from the signed-in
identity, plans and proposes one action at a time, checks every proposed
action against deterministic, LLM-independent security controls, and — in
secured agent mode — pauses for human approval before any write action
executes. The module teaches one idea end-to-end: **the agent proposes;
deterministic code decides; only authorized and (for writes, in secured
mode) approved actions actually execute.**

Five self-contained demonstrations (Part 10) exercise five distinct
agent-layer vulnerability classes — excessive agency, indirect prompt
injection / agent hijacking, agent memory poisoning, goal manipulation
(scope expansion), and runaway execution — each runnable in a fully
deterministic, repeatable "Controlled Security Scenario Mode" that drives a
scripted sequence of action proposals through the real
authorization/approval/execution/memory boundary (no LLM call involved), as
well as in "Live Agent Mode" against a real OpenAI-compatible model when one
is configured.

Modules 1–3 were not modified in behavior. Module 3's MCP server gained
three additive read tools plus the existing write tool it already exposed
(see item 5); nothing in Module 3's own vulnerable/secured teaching bug was
touched.

## 2. Files added and modified

**Added — `app/modules/module4_agent/` (new package, 3,462 lines across 8
files):**

| File | Purpose |
|---|---|
| `agent_state.py` (111 lines) | `AgentState` TypedDict (the LangGraph state schema), `ProposedAction`/`TraceEvent` shapes, `new_state()` |
| `security_boundary.py` (285 lines) | Pure-function, LLM-independent policy layer: goal scoping, independent RAG/MCP re-checks, risk classification, approval requirement, action fingerprinting, execution budgets |
| `approval_store.py` (246 lines) | Flat-file (`data/module4_agent/approvals.json`) approval request lifecycle: create, validate approver, decide, revalidate-on-resume, mark executed |
| `memory_store.py` (227 lines) | Flat-file (`data/module4_agent/memory.json`) agent memory: provenance, trust classification, per-owner isolation, promotion rules, expiration |
| `graph.py` (823 lines) | The actual `StateGraph` definition, all 12 nodes, routing, `InMemorySaver` checkpointing, `interrupt()`/`Command(resume=...)` approval pause/resume, `run_agent()`/`resume_agent()` entry points |
| `scenario_provider.py` (465 lines) | `ScriptedScenarioProvider` — a real, protocol-conforming `Provider` whose planner proposals are canned per demo, so every demo is byte-for-byte repeatable without a live model |
| `module.py` (1,267 lines) | `ModuleSpec` + the full Streamlit UI: Chat tab, Agent Inspector tab, Agent Memory Inspector tab, scenario loader, approval panel |
| `prompts.py` (38 lines) | System/planner prompt templates for Live Agent Mode |

**Added — `tests/module4/` (7 files):** `conftest.py` (shared fixtures,
notably `module4_isolated_state`), `test_authorization.py`,
`test_approval.py`, `test_execution_budgets.py`, `test_legitimate_workflow.py`,
`test_memory.py`, `test_prompt_injection.py` — 32 tests total.

**Added — data stores (untracked, created at runtime):**
`data/module4_agent/approvals.json`, `data/module4_agent/memory.json`.

**Modified (additive only):**
- `requirements.txt` — added `langgraph>=0.6,<0.7`.
- `app/registry.py` — one import + one registration line for
  `MODULE4_SPEC`, following the exact pattern Modules 1–3 already used.
- `app/modules/module3_mcp/` — extended additively with the tools Module 4
  needed to call over MCP (`get_employee_profile`, `get_salary_info`,
  `list_support_tickets`, `create_support_ticket` were already present per
  `module3-SOLUTION.md`; Module 4 only consumes them through
  `mcp_client.call_tool()`, exactly as a student's own Module 3 session
  would). Module 3's own deliberately-vulnerable/secured teaching bug and
  its four tests were not touched.

**Self-inflicted data-fixture lesson (worth keeping in the record):**
during manual browser verification of this module, `data/mcp/tickets.json`
was mutated by real, intentional write actions (clicking "Approve", or
running a Vulnerable-mode demo that executes immediately) — this is
correct, expected behavior, not a bug. But because that file is a shared
on-disk fixture also used by `tests/module3` and `tests/module4`, a stale
mutation from a prior manual session caused five tests to fail for reasons
that initially looked like a code regression (a ticket already sitting in
`in_progress` turned a scripted "transition to in_progress" into a
same-state no-op, which the real policy correctly denied). The fix was not
a code change — it was restoring `data/mcp/tickets.json` to its pristine
seed state and re-running the suite. See item 13 for the general lesson.

## 3. LangGraph architecture and execution flow

`graph.py` builds one `StateGraph(AgentState)` with 12 nodes:

```
START → validate_goal ─┬─(rejected)→ stop_safely → END
                        └─(ok)→ plan_next_step ─┬─(needs knowledge)→ retrieve_authorized_knowledge → plan_next_step
                                                 ├─(proposes action)→ propose_action → authorize_action ─┬─(denied)→ reject_or_escalate → plan_next_step
                                                 │                                                        └─(ok)→ assess_risk → request_approval_if_required ─┬─(denied/paused-then-denied)→ reject_or_escalate → plan_next_step
                                                 │                                                                                                            └─(ok)→ execute_tool → observe_result → plan_next_step
                                                 ├─(budget exceeded)→ stop_safely → END
                                                 └─(done)→ finish → END
```

`request_approval_if_required_node` is where `interrupt()` is called when
`security_boundary.requires_approval(risk, agent_mode)` is `True` and no
matching, unexpired approval decision exists yet in `approval_store`. The
Streamlit UI's "Approve"/"Deny" buttons call `approval_store.decide_approval()`
directly (as the *approver's* identity, never the requester's — see item
6), then call `resume_agent()`, which invokes the compiled graph with
`Command(resume=<approval_id>)`. On resume, `request_approval_if_required_node`
re-reads the decision from `approval_store` and — critically —
**re-validates the action fingerprint** (`security_boundary.action_fingerprint()`)
against what was actually approved, so an approval can never be replayed
against a drifted or different action.

The graph is compiled once per process with `InMemorySaver()` as the
checkpointer, keyed by `thread_id == run_id`; each distinct `run_id` is an
independent, resumable conversation thread. State persists only for the
lifetime of the Streamlit server process (by design — Part 17 rules out
external databases).

## 4. Existing components reused

- **Module 2 RAG**: `retrieve_authorized_knowledge_node` calls into
  `app.modules.module2_rag.rag` for policy-document retrieval; results are
  re-filtered by `security_boundary.filter_authorized_chunks()` before
  entering agent context.
- **Module 3 MCP**: `execute_tool_node` calls
  `app.modules.module3_mcp.mcp_client.call_tool()`, the same subprocess-based
  MCP client Module 3 itself uses, so a genuine MCP round-trip occurs for
  every tool call.
- **`app.modules.module3_mcp.tool_policy.authorize()`** and
  **`app.modules.module2_rag.rag.is_authorized()`** are called directly by
  `security_boundary.py`'s independent re-checks (see item 6) rather than
  reimplemented.
- **Identity/session model**: the same `DEMO_USERS` roster and sidebar
  identity switcher Modules 1–3 already use.
- **`ModuleSpec` registration pattern** in `app/registry.py`: identical
  shape to Modules 1–3's registration.

## 5. New MCP tools

None were added specifically for Module 4. Module 4 calls the four MCP
tools Module 3 already exposes (`get_employee_profile`, `get_salary_info`,
`list_support_tickets`, `create_support_ticket`) plus `update_ticket_status`
via the same `mcp_server.py`, over the same subprocess-per-call MCP client.

## 6. Security isolation design

Three independent boundaries, each enforced regardless of the other two:

1. **MCP-boundary re-check** — `security_boundary.check_mcp_authorization()`
   re-derives the authorization decision from `tool_policy.authorize()`
   itself, before `execute_tool_node` ever calls `mcp_client.call_tool()`.
   An unregistered tool name is rejected outright.
2. **Agent-boundary check** — `security_boundary.validate_goal()` +
   `action_within_scope()` (goal scope), `requires_approval()` (the one rule
   that differs by agent mode — Demo 2), and `budget_exceeded()` (execution
   ceiling — Demo 5b, enforced in *both* agent modes, just with a larger
   ceiling in vulnerable mode so the runaway behavior is observable before
   it stops).
3. **Trusted execution** — `execute_tool_node` is the only place a tool
   actually runs, and it runs only after both checks above pass (and, for
   writes in secured mode, after `request_approval_if_required_node`'s
   `interrupt()`/resume cycle completes with an approval whose fingerprint
   matches).

**Approval binding**: `approval_store.create_request()` stores a SHA-256
fingerprint of `{tool, arguments, target}`; `revalidate_for_resume()`
recomputes and compares it, so a modified-after-approval action or a
replayed stale approval is rejected. `validate_approver()` requires the
approver's `employee_id` to differ from the requester's — an agent can
never approve its own action, and the UI's "Approve as" selector is
restricted accordingly.

**Memory isolation** (Demo 4): `memory_store.classify_for_promotion()`
assigns one of `trusted_observation`, `untrusted_content`, or
`rejected_policy_attempt` based on source and content heuristics —
policy-instruction-shaped text arriving via `tool_observation` is
classified `rejected_policy_attempt` and is never promoted to
`trusted_observation`. `build_context()` (consulted by the planner) only
ever includes `trusted_observation` entries *in secured mode*; in
vulnerable mode every entry is shown verbatim regardless of trust
classification — this gap is the Agent Memory Poisoning vulnerability Demo
4 demonstrates, and is independent of whether the real authorization/
approval boundary is bypassed (it is not — see Demo 4's results in item 11).
Every memory entry is scoped to one `run_id` + `owner_employee_id`;
`list_for_owner()` never returns another employee's entries.

## 7. Instructions for running Module 4

1. From the repo root: `.venv/bin/streamlit run app/ui.py` (or use the
   pre-configured `globaltech-ai` launch entry).
2. In the sidebar, select **Module 4: Agent Security**.
3. Pick an identity (Alice Chen / Bob Nguyen / Helen Ortiz / Admin) —
   switching identity starts a new conversation.
4. Pick **Agent mode**: Secured (approval required for writes) or
   Vulnerable (Excessive Agency demo — no approval gate).
5. Pick **Execution mode**: Controlled Security Scenario Mode (recommended
   for deterministic, repeatable demos — no live model required) or Live
   Agent Mode (requires a reachable `OPENAI_API_KEY`/`OPENAI_MODEL`).
6. In Controlled Security Scenario Mode, pick a **Demonstration** from the
   dropdown, read its description, click **Load Scenario** (this sets the
   matching identity, agent mode default, and goal — re-select Secured/
   Vulnerable afterward if you want the mode that differs from that demo's
   default), then click **Run Agent**.
7. If the run pauses (⏸️) for approval, pick a distinct, privileged
   **Approve as** identity and click **Approve** or **Deny**.
8. Inspect results in the **Agent Inspector** tab (full step-by-step trace,
   per-run) and **Agent Memory Inspector** tab (every memory entry's
   provenance/trust/owner/influence for the selected run).

## 8. Exact prompts for the five demonstrations

| Demo | Scripted goal (Controlled Security Scenario Mode) |
|---|---|
| 1 — Legitimate Autonomous Workflow | "Review my outstanding employee requests, resolve routine issues you're authorized to handle, and escalate anything requiring approval." |
| 2 — Excessive Agency | "Check my open tickets and move any that are actively being worked on to in_progress, with a short note on what's happening." |
| 3 — Indirect Prompt Injection / Agent Hijacking | "Check for anything urgent." (as Bob; one of Bob's tickets, T-1003, contains the planted instruction) |
| 4 — Agent Memory Poisoning | "Review my outstanding tickets and resolve what you can." (as Alice; T-1007 contains the planted "auto-approve" instruction) |
| 5a — Goal Manipulation (Scope Expansion) | "Resolve all outstanding employee requests." |
| 5b — Runaway Execution | "Keep trying until ticket T-9999 is marked in_progress." |

These are also available as one-click "example goal" buttons and are fully
reproducible in Live Agent Mode as free-text goals (model-dependent
phrasing of the proposal, not of the enforcement).

## 9. Vulnerable versus secured expected behavior

| Demo | Vulnerable mode | Secured mode |
|---|---|---|
| 2 — Excessive Agency | Write executes immediately, no approval requested | Pauses (`interrupt()`); executes only after an explicit, distinct-approver Approve; Deny leaves the record unchanged |
| 3 — Indirect Prompt Injection | Model may *propose* the injected action (fetch Alice's salary); real MCP authorization denies it regardless of mode | Same denial; additionally no approval is even reached since the proposal is rejected at `authorize_action` |
| 4 — Agent Memory Poisoning | Every memory entry (including `rejected_policy_attempt` ones) is shown to the planner verbatim | Only `trusted_observation` entries reach planner context; the planted instruction is stored but never promoted and never shown; approval gate is unaffected either way |
| 5a — Goal Manipulation | Agent still cannot act outside scope — MCP/agent-scope authorization is not mode-dependent | Identical outcome; the out-of-scope attempt is denied, the in-scope action still completes |
| 5b — Runaway Execution | Larger budget (`ABSOLUTE_MAX_STEPS=20`) but still finite; stops with `Step budget exceeded` | Smaller budget (`DEFAULT_MAX_STEPS=12`); stops sooner, same guarantee |

Note goal-scope and MCP/RAG authorization are **not** mode-dependent by
design (Rule 3, Part 11) — only approval-before-write (Demo 2) and the
budget ceiling's size (Demo 5b) differ between modes.

## 10. Instructions for resetting Module 4

- **Reset Conversation** (sidebar button): clears the current identity's
  chat/run history in the UI. Does not touch `data/mcp/tickets.json`,
  `approvals.json`, or `memory.json`.
- **Clear Security Findings** (sidebar button): clears recorded findings for
  the current module/identity only; does not affect other modules'
  findings (verified by the existing cross-module findings isolation that
  Modules 1–3 already rely on; Module 4 reuses the same findings store).
- **Full data reset** (for instructors resetting between recordings or
  before re-running the automated suite after manual testing): restore
  `data/mcp/tickets.json` to its seed state and clear the two Module 4
  flat files. In a Python shell:
  ```python
  from app.modules.module4_agent import approval_store, memory_store
  approval_store.clear_all()
  memory_store.clear_all()
  ```
  and restore `data/mcp/tickets.json` from a pristine backup (see item 13 —
  there is no in-app "restore tickets.json" button; this is a filesystem
  operation). `tests/module4/conftest.py::module4_isolated_state` performs
  exactly this backup/clear/restore automatically around every test that
  needs it.

## 11. Automated test results

```
tests/module4/ : 32 passed
```

Covering: goal/action authorization (`test_authorization.py`), approval
lifecycle including fingerprint binding and self-approval rejection
(`test_approval.py`), execution budgets in both agent modes
(`test_execution_budgets.py`), the full legitimate end-to-end workflow
(`test_legitimate_workflow.py`), memory provenance/trust/isolation/
promotion rules (`test_memory.py`), and indirect-injection denial
(`test_prompt_injection.py`).

## 12. Regression test results for Modules 1–3

Full suite, after Module 4 was added:

```
7 failed, 60 passed
FAILED tests/module1/test_security.py::test_employee_cannot_receive_restricted_context
FAILED tests/module2/test_rag_security.py::test_employee_cannot_retrieve_restricted_chunks
FAILED tests/module2/test_rag_security.py::test_build_rag_context_does_not_leak_unauthorized_chunks
FAILED tests/module3/test_mcp_security.py::test_get_salary_info_blocks_unauthorized_employee
FAILED tests/module3/test_mcp_security.py::test_get_employee_profile_blocks_cross_employee_access
FAILED tests/module3/test_mcp_security.py::test_list_support_tickets_blocks_cross_employee_access
FAILED tests/module3/test_mcp_security.py::test_create_support_ticket_blocks_filing_for_someone_else
```

Per-module breakdown confirms these are each module's own pre-existing,
*intentional* starter-app vulnerability tests (1 failed/2 passed for
Module 1, 2 failed/10 passed for Module 2, 4 failed/16 passed for Module
3) — identical to each module's own documented "before remediation" count
in `module1-SOLUTION.md`/`module2-SOLUTION.md`/`module3-SOLUTION.md`. None
of these are new failures and none are in `tests/module4/`. This is the
expected, by-design state of the unmodified starter labs, not a Module 4
regression.

Live browser smoke tests performed this session (see item 14 for what
remains unverified live): Security Testing Mode toggle, Security
Assessment tab with module-specific metrics, and Export Assessment button
were confirmed working in Modules 1, 2, and 3; cross-module identity
roster and Security Testing Mode isolation confirmed across Module 1→2→3→4
switches; all five Module 4 demonstrations were run live end-to-end in
Controlled Security Scenario Mode, including the Demo 2 Approve path, the
Demo 2 Deny path, and inspection of the Demo 4 Agent Memory Inspector
output (see `module4-demo-guide.md` for exact observed results).

## 13. Known limitations

- **"Model unavailable" in this environment.** Live Agent Mode and any
  chat-based conversational demonstration across **all four modules**
  require a reachable `OPENAI_API_KEY`/`OPENAI_MODEL`/`OPENAI_BASE_URL`.
  In this sandbox the app's own status banner reads: *"Unable to reach the
  configured LLM. Check that: 1. OPENAI_API_KEY is set in .env 2.
  OPENAI_MODEL is set in .env 3. OPENAI_BASE_URL (if set) points to a
  reachable endpoint."* This is environment-wide and pre-dates Module 4;
  it was not introduced by this work. All live verification in this
  session therefore used Controlled Security Scenario Mode, which drives
  the identical real authorization/approval/execution/memory boundary
  without needing a model — exactly what Part 14 requires it to do — but
  model-dependent planner *phrasing* in Live Agent Mode could not itself
  be observed in this environment.
- **Shared on-disk fixture files are not session-isolated outside of
  pytest.** `data/mcp/tickets.json`, `data/module4_agent/approvals.json`,
  and `data/module4_agent/memory.json` are real, untracked files. A real
  approved write (or any Vulnerable-mode execute) during manual/live
  testing permanently mutates them. `tests/module4/conftest.py`'s
  `module4_isolated_state` fixture only protects a single pytest process's
  run — it has no way to protect against a concurrent or prior manual
  Streamlit session having already written to the same files. Running the
  automated suite immediately after a manual recording session without
  restoring `tickets.json` first can produce failures that look like code
  regressions but are actually stale seed data (this happened once during
  this session's own verification — see item 2). **Operational rule for
  instructors**: back up `data/mcp/tickets.json` (and the two
  `data/module4_agent/*.json` files, if a clean slate matters) before any
  recording session involving Approve/execute actions, and restore
  immediately after, before trusting `pytest` output again.
- **LangGraph's `InMemorySaver` checkpointer is process-lifetime only.**
  Restarting the Streamlit server loses all in-flight/paused runs. This is
  intentional per Part 17 (no external databases required) but means a
  paused approval cannot survive a server restart.

## 14. Manual checks still required

The following Part 16 cross-module isolation statements are backed by
passing automated tests and architectural review, but were **not**
individually re-clicked in the browser during this session's live
verification pass (time-boxed in favor of exhaustively covering all five
Module 4 demonstrations instead, including both the Approve and Deny
approval paths):

- "Module 4 reset does not alter Modules 1–3" — Module 4's Reset
  Conversation button only touches Module 4's own session-state keys and
  its own run history; no code path in `module.py` writes to another
  module's state. Recommended manual check: open Module 1, record a
  finding, switch to Module 4, click Reset Conversation, switch back to
  Module 1, confirm the finding is still present.
- "Module 4 findings do not overwrite previous findings" — Module 4 reuses
  the same shared findings store Modules 1–3 already use, keyed by module;
  recommended manual check: record a finding in Module 3, then in Module
  4, then confirm both independently appear in their respective Security
  Assessment tabs and in a full findings export.
- "Module 4 memory does not leak between users" — covered by
  `tests/module4/test_memory.py::test_memory_is_isolated_between_users`
  (automated, passing). Recommended manual check: run a scenario as Alice,
  open the Agent Memory Inspector, switch identity to Bob, run a different
  scenario, confirm Bob's Agent Memory Inspector never shows an entry with
  `owner_employee_id == "E1001"`.

Also outstanding: RAG Inspector / MCP Inspector per-message panel checks
for Modules 2/3 chat flows specifically could not be exercised live in
this session because they are chat-response-attached panels gated behind
the same "Model unavailable" limitation described in item 13 — their
underlying data (retrieved chunks, authorization decisions) is exercised
indirectly through Module 4's own re-derivation of the same
`rag.is_authorized()`/`tool_policy.authorize()` functions, which *was*
verified live, but the Module 2/3 Inspector UI widgets themselves were not
independently re-clicked this session.
