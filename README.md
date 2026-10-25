# GlobalTech AI Security Engineering

> 🎓 **This is the companion repository for the Udemy course [The AI Security Engineer Career Masterclass](https://www.udemy.com/course/the-ai-security-engineer-career-masterclass/?referralCode=DA52F79319AE292A3696).** Each module here is the hands-on lab for a section of the course.

A **deliberately vulnerable** AI application for learning AI security engineering hands-on. It plays the part of *GlobalTech AI*, an internal employee assistant at a fictional company, and is built as four self-contained lab **modules** inside one Streamlit app. You switch between modules from the sidebar; there are no separate projects to run.

Each module teaches one way AI applications get security wrong: you attack it in the browser, record what you find, read the code responsible, and (in Modules 1–3) fix it so the security tests pass.

All company names, projects, people and data are fictional.

> **Do not deploy this application.** The starter code is insecure on purpose. That is the lab.

## Modules

| Module | What it teaches | Student guide |
|---|---|---|
| **1. Prompt Security** | Handing an LLM restricted context and relying on the system prompt to keep it secret does not work. | [labs/01-prompt-security.md](labs/01-prompt-security.md) |
| **2. RAG Security** | In a retrieval pipeline, semantic relevance is not the same thing as authorization, and retrieved documents can carry prompt injections. | [labs/02-rag-security.md](labs/02-rag-security.md) |
| **3. MCP Security** | A correctly computed authorization decision does nothing unless the code that calls a tool checks it before acting. | [labs/03-mcp-security.md](labs/03-mcp-security.md) |
| **4. Agent Security** | An autonomous agent must only *propose* actions. The application re-authorizes every step, gates writes behind human approval, treats tool output and memory as untrusted, caps execution, and must log what happened. | [notes/04-agent-security-notes.md](notes/04-agent-security-notes.md) |

Module 4 runs six scripted demonstrations: a legitimate workflow, **excessive agency**, **action hijacking** (indirect prompt injection), **memory poisoning**, **goal manipulation** (scope expansion) and **runaway execution**. It closes with an exercise on **logging agentic actions**. Unlike Modules 1–3 it has no single planted bug to fix. Instead, a **Secured / Vulnerable** agent-mode switch lets you compare both behaviours side by side.

## What using the app looks like

1. Pick a **module** and a **demo identity** in the sidebar. Modules 1–2 use an employee (Alice) and an executive (Sarah). Modules 3–4 use two employees (Alice and Bob), an HR user (Helen) and an Admin.
2. Chat with the assistant as that identity and try to make it reveal or do things that identity shouldn't be allowed to.
3. Turn on **Security Testing Mode** to record findings. A **Security Assessment** tab appears, and **Export Assessment** writes a markdown report of your findings.
4. Use the inspectors to see what actually happened behind the chat: the **RAG Inspector** panel in Module 2 shows retrieved chunks and their authorization, and the **Agent Inspector** and **Agent Memory Inspector** tabs in Module 4 show the step-by-step trace and memory.
5. Read the code behind the behaviour and run the deterministic tests.

Module 4 also has **Controlled Security Scenario Mode**, which plays a fixed script through the real security controls with no model call. Every demo there gives the same result each time, and it works even without an LLM configured.

## Repository layout

```text
app/
  ui.py, main.py         Streamlit and CLI entry points
  registry.py            the list of modules
  core/                  shared shell: UI, CLI, LLM providers, identities, audit log, findings
  modules/
    module1_prompt_security/
    module2_rag/         TF-IDF retrieval over data/module2_rag/documents/
    module3_mcp/         a real MCP server and client (stdio subprocess)
    module4_agent/       a LangGraph agent with approval, memory and budget controls
data/                    seed data: RAG documents, MCP employees/salaries/tickets
labs/                    student lab handouts (Modules 1–3)
notes/                   course notes, one Markdown file per module
instructor/              solutions and the Module 4 demo guide (attempt the labs first)
tests/                   deterministic security tests, one folder per module
```

## Requirements

- **Python 3.11 or newer**: it has been run on 3.15.
- **An LLM for chat**, either of:
  - **Ollama** (free, runs locally): default model `qwen3:4b`, about a 2.5 GB download.
  - **An OpenAI-compatible API**: an API key and a model name.
- **Git**, to clone the repository.

Module 2's retrieval uses a local TF-IDF index, so no embedding model is needed. You can run the tests and Module 4's scripted demos with no LLM at all.

## Installation

### 1. Get the code

```bash
git clone https://github.com/taimurijlal/AI-Security-Engineering-Class.git
cd AI-Security-Engineering-Class
```

### 2. Install Python

If `python3 --version` shows something older than 3.11, install a current version from [python.org](https://www.python.org/downloads/), or with your package manager (for example `brew install python@3.12` on macOS).

### 3. Create a virtual environment and install dependencies

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

If `python3` is still the old system version, use the full versioned name instead, for example `python3.12 -m venv .venv`.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell refuses to run `Activate.ps1` because scripts are disabled, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again, or use Command Prompt and run `.venv\Scripts\activate.bat`.

Activate the environment again (`source .venv/bin/activate`) in every new terminal before running the commands below.

### 4. Configure the model

Edit `.env`. The template, [.env.example](.env.example), has every setting:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `openai` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | where Ollama is listening |
| `OLLAMA_MODEL` | `qwen3:4b` | any Ollama chat model |
| `OPENAI_API_KEY` | — | required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | — | e.g. `gpt-4o-mini` |
| `OPENAI_BASE_URL` | blank | leave blank for OpenAI itself; set it only for another OpenAI-compatible provider |

**Option A — Ollama (default).** Install Ollama from [ollama.com](https://ollama.com), start it, then pull the model:

```bash
ollama pull qwen3:4b
```

If your machine can't comfortably run that model, pull a smaller chat model and set `OLLAMA_MODEL` to its name.

**Option B — OpenAI-compatible API.** Set the following in `.env`:

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

`.env` is git-ignored. Never commit it or paste API keys into source files.

### 5. Verify the setup

```bash
python -m app.core.check_setup
```

For Ollama this checks that the server is reachable and the model is pulled. For OpenAI it checks that the required settings are present; the first chat message confirms the key actually works.

### 6. (Optional) Build the Module 2 index

```bash
python -m app.modules.module2_rag.ingestion
```

The app builds the index automatically on first use. Rerun this command whenever you edit the documents under `data/module2_rag/documents/`.

## Running the app

### Browser UI

```bash
streamlit run app/ui.py
```

This opens GlobalTech AI at `http://localhost:8501`. Use the **MODULE** switcher at the top of the sidebar to pick a lab. Each module keeps its own conversation, identity and Security Testing Mode state.

### Command line

```bash
python -m app.main
```

This asks you to choose a module and an identity, then starts a terminal chat using the same backend. The CLI fully supports **Modules 1 and 2** only. Modules 3 and 4 need the browser UI, because their tool calls, approval prompts and agent runs only work there; in the CLI they just print a note pointing you to `streamlit run app/ui.py`.

## Running the security tests

```bash
pytest -q
```

The tests are deterministic and need no LLM. They check the application's security controls, not model responses. On the starter code, **7 tests fail on purpose**: 1 in Module 1, 2 in Module 2 and 4 in Module 3. These are the vulnerabilities you fix in the labs. All 32 Module 4 tests should pass from the start.

Run one module's tests with `pytest -q tests/module1/` (or `module2/`, `module3/`, `module4/`).

## Files the app writes

These are created at runtime and ignored by git:

- `attack_log.jsonl`: every chat interaction across all modules, tagged by `module`.
- `security_findings.jsonl`: findings recorded in Security Testing Mode, tagged by `module`.
- `globaltech_*_assessment.md`: reports generated by **Export Assessment**.
- `data/module2_rag/index/rag_index.pkl`: Module 2's retrieval index (rebuilt if missing).
- `data/module4_agent/approvals.json` and `memory.json`: Module 4's approval records and agent memory (start empty if missing).

One committed file is also written to: `data/mcp/tickets.json`, the support-ticket store. Module 3's `create_support_ticket` adds tickets to it, and **Approve** or Vulnerable-mode runs in Module 4 change ticket statuses. Reset it with:

```bash
git checkout data/mcp/tickets.json
```

## Troubleshooting

- **The sidebar shows "○ Model unavailable".** Check that Ollama is running and the model has been pulled, or that the OpenAI settings in `.env` are correct, then rerun `python -m app.core.check_setup`. The app shows a plain-language error with a suggested fix. Module 4's Controlled Security Scenario Mode still works without a model.
- **More tests fail than the 7 expected.** You've probably changed the ticket data during manual testing. Reset `data/mcp/tickets.json` (see above) and rerun.
- **A Module 4 demo gives a different result than the notes.** Clicking **Load Scenario** resets the agent mode to that demo's default. Choose the mode you want *after* loading the scenario. Also reset the ticket data between repeated write demos.
- **`pip install` fails.** Check that the virtual environment uses Python 3.11 or newer (`python --version` with the environment active).

## For instructors

- [instructor/](instructor/) has a solution for each module.
- [notes/](notes/) has the course notes, one Markdown file per module (`01-` to `04-…-notes.md`). [04-agent-security-notes.md](notes/04-agent-security-notes.md) is the main self-guided material for Module 4, which has no lab handout yet.

## Architecture

Every module is a thin, isolated package under `app/modules/<module>/` that implements the `ModuleSpec` contract (`app/core/module_spec.py`). Shared mechanics live once in `app/core/` and are driven by each module's spec: the Streamlit and CLI shells, identities, the LLM provider abstraction, audit logging and Security Testing Mode findings. Adding a module means writing one `module.py` and registering it in `app/registry.py`; nothing in `app/core/` changes.

Module 3 runs a genuine [MCP](https://modelcontextprotocol.io/) server. `mcp_client.py` launches `app/modules/module3_mcp/mcp_server.py` as a real subprocess over the standard stdio transport, then discovers tools with `list_tools()` and invokes them with `call_tool()`, exactly as any MCP host would. The application, never the model, owns session identity: `mcp_client.call_tool()` always replaces any `requester_id`/`requester_role` the model proposes with the signed-in user from the current session before the call goes over the wire.

Module 4 is a real [LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph` agent (`app/modules/module4_agent/graph.py`) built on Modules 2 and 3. Each step goes: validate goal → plan → propose → authorize → assess risk → human approval (LangGraph `interrupt()`) → execute through Module 3's MCP client → observe. Only the plan and propose steps read model output. `security_boundary.py` re-applies `rag.is_authorized()` and `tool_policy.authorize()` itself, so the agent stays secure whether or not you have fixed Modules 2 and 3.

Logging is shared across modules: `attack_log.jsonl` and `security_findings.jsonl` live at the project root and every entry is tagged with a `module` field, so an assessment export can cover one module or all of them.

## A note on results

Prompt attacks are probabilistic: a payload that works against one model or one run may fail on another. That's why the tests check the application's controls rather than what a particular model says, and why Module 4's scripted mode exists for repeatable demonstrations.
