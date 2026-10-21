"""
Flat-file "system of record" for Module 3's fictional GlobalTech HR/IT data.

No database, no caching, no ORM -- each lookup reads the relevant JSON file
under data/mcp/ fresh off disk, and the one write action
(create_support_ticket) reads, appends, and rewrites the tickets file. This
is intentionally the simplest thing that behaves like a real backend an
MCP server would front: a place to read and write company data that
exists independently of any one conversation.

This module has no concept of authorization. It answers "what does the
record say" -- never "should the caller see this." That question is
app/modules/module3_mcp/tool_policy.py's job alone, exactly mirroring how
app/modules/module2_rag/rag.py separates retrieval (relevance) from
is_authorized() (permission).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "mcp"
EMPLOYEES_FILE = DATA_DIR / "employees.json"
SALARIES_FILE = DATA_DIR / "salaries.json"
TICKETS_FILE = DATA_DIR / "tickets.json"


def _load(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)
        fh.write("\n")


def find_employee(employee_id: str) -> dict[str, Any] | None:
    for record in _load(EMPLOYEES_FILE):
        if record["employee_id"] == employee_id:
            return record
    return None


def find_salary(employee_id: str) -> dict[str, Any] | None:
    for record in _load(SALARIES_FILE):
        if record["employee_id"] == employee_id:
            return record
    return None


def find_tickets_for(employee_id: str) -> list[dict[str, Any]]:
    return [t for t in _load(TICKETS_FILE) if t["employee_id"] == employee_id]


def find_ticket(ticket_id: str) -> dict[str, Any] | None:
    for record in _load(TICKETS_FILE):
        if record["ticket_id"] == ticket_id:
            return record
    return None


def create_ticket(employee_id: str, subject: str, description: str) -> dict[str, Any]:
    tickets = _load(TICKETS_FILE)
    existing_numbers = [
        int(t["ticket_id"].split("-")[1]) for t in tickets if t["ticket_id"].startswith("T-")
    ]
    next_number = max(existing_numbers, default=1000) + 1
    ticket = {
        "ticket_id": f"T-{next_number}",
        "employee_id": employee_id,
        "subject": subject,
        "description": description,
        "status": "open",
        "created_at": "2026-10-03",
    }
    tickets.append(ticket)
    _save(TICKETS_FILE, tickets)
    return ticket


def update_ticket(ticket_id: str, new_status: str, resolution_note: str) -> dict[str, Any] | None:
    """Additive write action for Module 4 (Agent Security): update an
    existing ticket's status and append a resolution note. Added for the
    agent lab's update_ticket_status tool; does not change create_ticket(),
    find_ticket(), or any other Module 3 behavior. Like create_ticket(),
    this function has no concept of authorization -- it answers "update the
    record" unconditionally. The caller (mcp_server.py's
    update_ticket_status tool) is responsible for checking
    tool_policy.authorize() before calling this, exactly as the comment at
    the top of this file describes for every other backend function.
    Returns None if ticket_id does not exist, without raising."""
    tickets = _load(TICKETS_FILE)
    updated: dict[str, Any] | None = None
    for ticket in tickets:
        if ticket["ticket_id"] == ticket_id:
            ticket["status"] = new_status
            ticket["resolution_note"] = resolution_note
            updated = ticket
            break
    if updated is None:
        return None
    _save(TICKETS_FILE, tickets)
    return updated
