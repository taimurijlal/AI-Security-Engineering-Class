# Solution — Open After Completing Lab 01

A simple remediation for `get_context()` is:

```python
def get_context(user_role: str) -> str:
    role = user_role.strip().lower()

    if role == "executive":
        return "\n\n".join([
            PUBLIC_CONTEXT,
            INTERNAL_CONTEXT,
            RESTRICTED_CONTEXT,
        ])

    if role == "employee":
        return "\n\n".join([
            PUBLIC_CONTEXT,
            INTERNAL_CONTEXT,
        ])

    if role == "contractor":
        return PUBLIC_CONTEXT

    return PUBLIC_CONTEXT
```

A contractor test could be:

```python
def test_contractor_receives_only_public_context():
    context = get_context("contractor")

    assert "GlobalTech provides" in context
    assert "Remote working" not in context
    assert "PROJECT ATLAS" not in context
```

## Why this is better

The vulnerable design sent restricted data to the model and depended on probabilistic model behaviour to prevent disclosure.

The remediated design makes an authorization decision **before** assembling model context.

This does not eliminate prompt injection. It reduces the impact by removing information the session is not authorized to access.
