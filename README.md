# GlobalTech AI Security Engineering

A deliberately vulnerable AI application for learning AI security engineering, built as a growing set of self-contained **modules** inside one app. Switch between modules from the sidebar — no need to run separate projects.

All company names, projects, people and data are fictional.

## Modules

| Module | Teaches | Lab |
|---|---|---|
| 1. Prompt Security | Why handing an LLM restricted context and relying on the system prompt to keep it secret does not work | [labs/01-prompt-security.md](labs/01-prompt-security.md) |
| 2. RAG Security | Why semantic relevance in a retrieval pipeline is not the same thing as authorization | [labs/02-rag-security.md](labs/02-rag-security.md) |
| 3. MCP Security | Why a correctly-computed authorization decision does nothing unless the code that calls a tool actually checks it before acting | [labs/03-mcp-security.md](labs/03-mcp-security.md) |
| 4. Agent Security | Why an autonomous agent must only *propose* actions: the application re-authorizes every step, gates writes behind human approval, and treats tool output and memory as untrusted | Instructor-led demo (see `instructor/module4-demo-guide.md`) |

Each module has its own instructor solution in `instructor/` (don't open until you've attempted the lab yourself).

## Architecture

Every module is a thin, isolated package under `app/modules/<module>/` that implements the `ModuleSpec` contract (`app/core/module_spec.py`). Shared mechanics — the Streamlit UI shell, the CLI shell, auth, the LLM provider abstraction, audit logging, and Security Testing Mode findings — live once in `app/core/` and are driven by each module's spec. Adding a module means writing one `module.py` and registering it in `app/registry.py`; nothing in `app/core/` changes.

Module 3 additionally runs a genuine [MCP](https://modelcontextprotocol.io/) server: `app/modules/module3_mcp/mcp_server.py` is launched by `mcp_client.py` as a real subprocess over the standard stdio transport, discovered via `list_tools()` and invoked via `call_tool()` exactly as any MCP host would, not a Python function dressed up and called an "MCP tool." The application — never the model — owns session identity: `mcp_client.call_tool()` always overwrites any `requester_id`/`requester_role` the model proposes with the authenticated `app.core.security.User` from the current session before the call goes over the wire.

Module 4 builds a real [LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph` agent (`app/modules/module4_agent/graph.py`) on top of Modules 2 and 3: validate goal → plan → propose → authorize → assess risk → human approval (LangGraph `interrupt()`) → execute through Module 3's MCP client → observe. Only the plan/propose steps read model output; `security_boundary.py` re-applies `rag.is_authorized()` and `tool_policy.authorize()` itself, so the agent stays secure whether or not you have fixed Modules 2 and 3. The sidebar offers **Controlled Security Scenario Mode** (scripted, deterministic planner, no LLM call — good for repeatable demos) and **Live Agent Mode** (real model), plus a **Secured** / **Vulnerable** agent-mode switch that toggles the approval gate for the Excessive Agency demo. Approvals and agent memory persist in `data/module4_agent/approvals.json` and `memory.json`.

Logging is unified: `attack_log.jsonl` and `security_findings.jsonl` live at the project root and every entry is tagged with a `module` field, so a Security Assessment export can cover one module or (via the shared findings store) the whole portfolio.

## Setup

### 1. Install Python

Python 3.11+ is recommended.

### 2. Install Ollama (recommended, free, local)

Install Ollama from its official website, then start it, and pull the default model:

```bash
ollama pull qwen3:4b
```

If your workstation cannot comfortably run that model, choose another Ollama chat model and set `OLLAMA_MODEL` in `.env`.

### 3. Set up the project

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell refuses to run `Activate.ps1` because scripts are disabled, run this once and try again: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Alternatively, use Command Prompt and run `.venv\Scripts\activate.bat`.

### 4. Verify connectivity

```bash
python -m app.core.check_setup
```

### 5. Build the Module 2 retrieval index

```bash
python -m app.modules.module2_rag.ingestion
```

(The app builds this automatically on first use if you forget, but it's good practice to run it explicitly after editing the document corpus under `data/module2_rag/documents/`.)

### 6. Launch the browser UI

```bash
streamlit run app/ui.py
```

This opens GlobalTech AI in your browser (default `http://localhost:8501`). Use the **MODULE** switcher at the top of the sidebar to pick a lab; each module keeps its own conversation, identity, and Security Testing Mode state.

If the sidebar shows **○ Model unavailable**, check that Ollama is running and the model above has been pulled. The app shows a plain-language error (no stack trace) with a suggested command.

## Alternative: the CLI

```bash
python -m app.main
```

Prompts you to choose a module, then a demo identity, then starts a terminal chat loop using the identical backend.

## Optional OpenAI-compatible provider

Update `.env`:

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

Leave `OPENAI_BASE_URL` blank to use the real OpenAI API. Only set it if pointing at a different OpenAI-compatible provider. Do not commit `.env` or API keys to source control.

## Run the security tests

```bash
pytest -q
```

The starter implementations of Modules 1–3 are deliberately vulnerable, so some authorization tests are expected to fail (1 in Module 1, 2 in Module 2, 4 in Module 3) until you remediate them during the labs. Module 4 ships with both its vulnerable and secured paths selectable at runtime, so all of its tests should pass from the start. Run a single module's tests with `pytest -q tests/module1/` (or `module2/`, `module3/`, `module4/`).

Module 4's end-to-end tests run the real MCP subprocess and write to `data/mcp/tickets.json`; the `module4_isolated_state` fixture backs up and restores it, along with the approval and memory stores.

## Local files this app creates

These are ignored by git (see `.gitignore`):

- `attack_log.jsonl` — every normal interaction across all modules, tagged by `module`
- `security_findings.jsonl` — findings recorded in Security Testing Mode, tagged by `module`
- `globaltech_prompt_security_assessment.md`, `globaltech_rag_security_assessment.md`, `globaltech_mcp_security_assessment.md`, `globaltech_agentic_ai_security_assessment.md` — generated by **Export Assessment**
- `data/module2_rag/index/rag_index.pkl` — Module 2's retrieval index (rebuilt automatically if missing)
- `data/module4_agent/approvals.json`, `data/module4_agent/memory.json` — Module 4's approval and agent-memory stores (start empty if missing)

This one is committed seed data that the app also writes to:

- `data/mcp/tickets.json` — Module 3's support-ticket store; `create_support_ticket` writes to it directly, so it can accumulate test tickets if you file them during the lab. Approve or Vulnerable-mode runs in Module 4 also write to it. Run `git checkout data/mcp/tickets.json` to reset it

## Important

Prompt attacks are probabilistic. A payload that works against one model or one run may fail on another. The deterministic security tests validate the application controls surrounding the model, not a particular model response.
