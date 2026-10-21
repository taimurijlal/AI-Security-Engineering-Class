# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GlobalTech AI Security Engineering: a **deliberately vulnerable** Python/Streamlit teaching app made of self-contained lab modules (1 Prompt Security, 2 RAG Security, 3 MCP Security, 4 Agent Security). All companies, people and data are fictional.

The parent `~/Desktop/CLAUDE.md` describes an unrelated Node.js banking API (npm, helmet, bcrypt, PCI DSS). Its rules and verify commands do not apply here.

## Commands

```bash
source .venv/bin/activate              # Python 3.11+; deps in requirements.txt
python -m app.core.check_setup         # verify LLM provider connectivity
python -m app.modules.module2_rag.ingestion   # rebuild Module 2 index after editing data/module2_rag/documents/
streamlit run app/ui.py                # browser UI (localhost:8501)
python -m app.main                     # CLI, same backend

pytest -q                              # all tests (pytest.ini sets pythonpath=. and testpaths=tests)
pytest -q tests/module3/               # one module
pytest -q tests/module4/test_approval.py::test_name   # single test
```

There is no lint or typecheck configured.

LLM provider is selected via `.env` (`LLM_PROVIDER=ollama` default with `OLLAMA_MODEL=qwen3:4b`, or `openai` with `OPENAI_API_KEY`/`OPENAI_MODEL`/optional `OPENAI_BASE_URL`). Tests are deterministic and validate application controls, not model responses.

## Deliberate vulnerabilities — do not "fix" unprompted

The starter code is intentionally insecure; that is the lab. Expected failing tests in starter state: 1 in Module 1, 2 in Module 2, 4 in Module 3. Each vulnerability follows the same pattern: a correct authorization check is computed (and often surfaced in the UI) but never enforced:
- Module 1: `module1_prompt_security/context.py` hands restricted context to every role and relies on the system prompt.
- Module 2: `module2_rag/rag.py` ranks/returns every chunk regardless of role; `is_authorized()` exists but isn't enforced. A corpus document (`internal/quarterly_business_update.txt`) contains an embedded prompt injection on purpose.
- Module 3: each tool in `module3_mcp/mcp_server.py` calls `tool_policy.authorize()` but executes the backend read/write regardless of `decision.allowed`.

Solutions live in `instructor/moduleN-SOLUTION.md` (plus `instructor/module4-demo-guide.md`); lab handouts in `labs/`.

## Architecture

- **Module contract**: each module is `app/modules/<module>/module.py` exporting `MODULE_SPEC: ModuleSpec` (`app/core/module_spec.py`), registered in `app/registry.py`. The shared shells (`app/core/ui_shell.py`, `app/core/cli_shell.py`) drive everything through the spec; `app/ui.py` and `app/main.py` are thin entry points. Adding a module should not require changes in `app/core/`. `ModuleSpec` has optional extension points (`handle_prompt`, `render_chat`, `extra_tabs`, `demo_users`, `custom_export_fn`) that default to the simple single-LLM-call flow used by Modules 1–2.
- **Shared core**: `llm.py` (`Provider` protocol: `generate(system_prompt, user_prompt, context)` / `health_check()`; Ollama and OpenAI impls; raises `LLMError`), `security.py` (`User`, `DEMO_USERS`), `audit.py` → `attack_log.jsonl`, `findings.py` → `security_findings.jsonl` + markdown assessment export. Both JSONL logs are at repo root and every entry is tagged with `module`.
- **Module 3 (MCP)**: `mcp_client.py` spawns `mcp_server.py` as a **real subprocess per call** over MCP stdio. `call_tool()` always overwrites `requester_id`/`requester_role` with the trusted session `User` — the model never supplies identity. Layering: `backend.py` (flat-file JSON under `data/mcp/`, no authz) ← `tool_policy.py` (pure authz decisions) ← `mcp_server.py`.
- **Module 4 (Agent)**: a real LangGraph `StateGraph` in `graph.py` (validate_goal → plan_next_step → propose → authorize → assess_risk → approval (LangGraph `interrupt()`) → execute via Module 3's `mcp_client` → observe). Principle: "the agent proposes, the application authorizes, the trusted execution layer enforces" — only the plan/propose nodes read model output, parsed into a `ProposedAction`. `security_boundary.py` is a pure-function re-authorization layer that reuses `rag.is_authorized()` and `tool_policy.authorize()` so Module 4 stays secure regardless of whether Modules 2/3 have been fixed. Never disable RAG/MCP authz to make an agent demo succeed. `scenario_provider.py` is a scripted `Provider` for repeatable demos; it must only script planner output, never fake execution/approval state. Flat-file stores: `data/module4_agent/approvals.json`, `memory.json`.

## Testing gotchas

- Module 3 tests call `mcp_server` functions in-process and isolate writes by monkeypatching `backend.TICKETS_FILE`.
- Module 4 end-to-end tests go through the MCP subprocess, so monkeypatching doesn't reach it. Use the `module4_isolated_state` fixture (`tests/module4/conftest.py`), which backs up and restores `tickets.json`, `approvals.json`, `memory.json` on disk.
- `data/mcp/tickets.json` accumulates tickets if `create_support_ticket` is exercised manually.
