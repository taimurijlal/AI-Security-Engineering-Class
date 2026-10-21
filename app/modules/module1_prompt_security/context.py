PUBLIC_CONTEXT = """
GLOBALTECH PUBLIC INFORMATION
GlobalTech provides cloud security and AI services to enterprise customers.
""".strip()

INTERNAL_CONTEXT = """
GLOBALTECH INTERNAL EMPLOYEE INFORMATION
Employees receive 25 days of annual leave.
Remote working is permitted up to three days per week with manager approval.
All employees must complete annual security awareness training.
""".strip()

RESTRICTED_CONTEXT = """
GLOBALTECH RESTRICTED INFORMATION — PROJECT ATLAS
GlobalTech is planning to acquire NovaTech for $240 million.
The planned acquisition has not been publicly announced.
Only members of the Executive and Legal teams are authorized to access
Project Atlas information.
""".strip()


def get_context(user_role: str) -> str:
    """
    DELIBERATELY VULNERABLE STARTER IMPLEMENTATION.

    Security flaw:
    Every role receives RESTRICTED_CONTEXT. The application relies on the
    language model's system prompt to prevent disclosure.

    Students will remediate this during the lab.
    """
    return "\n\n".join([
        PUBLIC_CONTEXT,
        INTERNAL_CONTEXT,
        RESTRICTED_CONTEXT,
    ])
