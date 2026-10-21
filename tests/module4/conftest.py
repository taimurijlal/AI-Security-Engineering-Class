"""
Shared pytest fixtures for Module 4 (Agentic AI Security) deterministic
tests -- Part 15 of the lab spec.

ISOLATION, AND WHY IT CANNOT USE MODULE 3'S monkeypatch(TICKETS_FILE)
PATTERN: tests/module3/test_mcp_security.py isolates writes by pointing
`backend.TICKETS_FILE` at a tmp_path copy via monkeypatch -- that works
there because every Module 3 test calls mcp_server functions (e.g.
srv.create_support_ticket(...)) directly, in-process. Module 4's
end-to-end tests instead drive the real app.modules.module4_agent.graph,
whose execute_tool_node calls app.modules.module3_mcp.mcp_client.call_tool(),
which -- per that module's own docstring -- spawns a brand-new `python
mcp_server.py` *subprocess* for every single call. That subprocess does
its own fresh `import backend`, so it reads/writes the real
data/mcp/tickets.json on disk no matter what the parent pytest process's
`backend.TICKETS_FILE` attribute has been monkeypatched to. The only
isolation that actually protects the real fixture file across a
subprocess boundary is backing up and restoring the file's bytes on disk,
which is what `module4_isolated_state` below does -- for tickets.json AND
for Module 4's own two flat-file stores (approvals.json, memory.json),
which are real files under data/module4_agent/ that any end-to-end
run_agent()/resume_agent() call writes to directly.

Tests that only call pure functions (security_boundary.py, tool_policy.py)
do not need this fixture at all -- they touch no files. Tests that call
approval_store.*/memory_store.* directly (no subprocess involved) still
need it, so one run's leftover records can never leak into another test's
assertions (list_for_run, etc.).
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.module3_mcp import backend
from app.modules.module4_agent import approval_store
from app.modules.module4_agent import memory_store as mem


@pytest.fixture()
def module4_isolated_state():
    """Back up the three real flat files Module 4 end-to-end tests can
    touch (data/mcp/tickets.json, data/module4_agent/approvals.json,
    data/module4_agent/memory.json), clear the two Module 4 stores so
    this test starts from an empty slate, run the test, then restore all
    three files to exactly their pre-test bytes -- so running this suite
    never leaves a mutated ticket, a stray approval record, or a stray
    memory entry behind for a later test run or for a real demo session."""
    tickets_backup = backend.TICKETS_FILE.read_text(encoding="utf-8")
    approvals_backup = approval_store.STORE_FILE.read_text(encoding="utf-8") if approval_store.STORE_FILE.exists() else None
    memory_backup = mem.STORE_FILE.read_text(encoding="utf-8") if mem.STORE_FILE.exists() else None

    approval_store.clear_all()
    mem.clear_all()
    try:
        yield
    finally:
        backend.TICKETS_FILE.write_text(tickets_backup, encoding="utf-8")
        if approvals_backup is not None:
            approval_store.STORE_FILE.write_text(approvals_backup, encoding="utf-8")
        else:
            approval_store.clear_all()
        if memory_backup is not None:
            mem.STORE_FILE.write_text(memory_backup, encoding="utf-8")
        else:
            mem.clear_all()


@pytest.fixture()
def run_id() -> str:
    """A fresh, unique run_id per test -- graph.py's InMemorySaver
    checkpointer is keyed by thread_id == run_id, and the compiled graph
    is a module-level cached singleton shared across the whole pytest
    process, so reusing an id across tests would resume a previous test's
    paused/finished run instead of starting a clean one."""
    return f"test-run-{uuid.uuid4()}"
