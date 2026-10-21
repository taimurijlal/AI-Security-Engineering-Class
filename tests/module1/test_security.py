from app.modules.module1_prompt_security.context import get_context


def test_employee_receives_internal_context():
    context = get_context("employee")
    assert "Remote working" in context
    assert "security awareness" in context


def test_employee_cannot_receive_restricted_context():
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    Do not weaken this test. Fix the application.
    """
    context = get_context("employee")
    assert "PROJECT ATLAS" not in context
    assert "NovaTech" not in context
    assert "$240 million" not in context


def test_executive_can_receive_restricted_context():
    context = get_context("executive")
    assert "PROJECT ATLAS" in context
    assert "NovaTech" in context
