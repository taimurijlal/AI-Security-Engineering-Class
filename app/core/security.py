from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    email: str
    role: str
    # Optional stable identifier used by Module 3 (MCP Security) to look up
    # records (e.g. employee/salary/ticket rows) by a trusted, application-
    # issued id rather than anything the model infers from chat text.
    # Defaults to None so Module 1 and Module 2 -- which never set it -- are
    # completely unaffected.
    employee_id: str | None = None


DEMO_USERS = {
    "employee": User("alice@globaltech.example", "employee"),
    "executive": User("sarah@globaltech.example", "executive"),
}


def normalize_role(role: str) -> str:
    return role.strip().lower()
