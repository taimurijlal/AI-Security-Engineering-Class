# Instructor Solution — GlobalTech AI V2 (RAG Security)

This file is intentionally kept in its own `instructor/` directory, separate
from `README.md` and `labs/02-rag-security.md`. It is not linked from the
normal student-facing lab flow. Students are told in `labs/02-rag-security.md`
to attempt Phase 9 themselves before opening this file.

Do not fold this content back into the top-level README or the lab
document.

---

## The vulnerability, precisely

In the starter `app/modules/module2_rag/rag.py`:

- `retrieve_chunks()` scores **every** chunk in the index against the query
  and returns the top `k`, regardless of `user_role`.
- `is_authorized()` is correct and is even used to compute an `authorized`
  flag on each result -- but that flag is purely observational. Nothing
  reads it before deciding what to return.
- `build_rag_context()` concatenates every retrieved chunk's text, so
  whatever `retrieve_chunks()` returned reaches the model, authorized or
  not.

The fix belongs in exactly one place: the candidate pool `retrieve_chunks()`
scores against must already be limited to chunks the requesting role is
authorized for, *before* similarity ranking happens. This is the RAG
equivalent of V1's fix living entirely inside `get_context()`.

## Reference fix

```python
# app/modules/module2_rag/rag.py

def retrieve_chunks(
    query: str,
    index: RagIndex | None = None,
    top_k: int = DEFAULT_TOP_K,
    user_role: str | None = None,
) -> list[RetrievedChunk]:
    if index is None:
        index = get_index()

    query_vector = embed_query(query, index)

    # SECURE: authorization constrains the candidate pool before ranking,
    # instead of being computed afterward for display only. A chunk the
    # requesting role is not authorized for is never scored, never ranked,
    # and therefore never returned -- regardless of how semantically
    # relevant it is to the query.
    candidates = zip(index.chunks, index.chunk_vectors)
    if user_role is not None:
        candidates = (
            (chunk, vector)
            for chunk, vector in candidates
            if is_authorized(user_role, chunk.allowed_roles)
        )

    scored = [
        (chunk, _cosine_similarity(query_vector, vector))
        for chunk, vector in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    results: list[RetrievedChunk] = []
    for chunk, similarity in scored[:top_k]:
        results.append(RetrievedChunk(chunk=chunk, similarity=similarity, authorized=True))
    return results
```

`retrieve_documents()` and `build_rag_context()` do not need to change at
all. `retrieve_documents()` already forwards to `retrieve_chunks()`, and
`build_rag_context()` already just serializes whatever it is given --
once `retrieve_chunks()` stops returning unauthorized chunks, there is
nothing left for `build_rag_context()` to leak. This is worth pointing out
to students explicitly: **fixing the single upstream function fixes both
failing tests**, which is the same "move the decision upstream, once"
lesson as V1.

A student who instead tries to filter inside `build_rag_context()` (post-hoc
filtering) will pass `test_build_rag_context_does_not_leak_unauthorized_chunks`
but should be pushed to explain why that is a weaker design: it means the
unauthorized chunk was still retrieved, still occupied a `top_k` slot
(potentially crowding out a real authorized result), and still exists in
memory as a "the model just didn't use it" risk rather than a structural
guarantee. The intended fix prevents the chunk from being a candidate at
all.

## Expected test results

Before remediation:

```
2 failed, 10 passed
FAILED tests/module2/test_rag_security.py::test_employee_cannot_retrieve_restricted_chunks
FAILED tests/module2/test_rag_security.py::test_build_rag_context_does_not_leak_unauthorized_chunks
```

After remediation:

```
12 passed
```

## Why this does not eliminate prompt injection

The Phase 5 / Phase 6 exercises (indirect injection and knowledge poisoning
via `quarterly_business_update.txt`) are **not** fixed by this change, and
should not be. That document is `internal`, and an employee is correctly
authorized to retrieve it. The remediation in this lab is scoped to the
authorization boundary, not to content trustworthiness. If an instructor
wants to extend the lab, a reasonable follow-on discussion (not required for
this lab's tests to pass) is: how would you detect or neutralize
instruction-like content inside a retrieved chunk before it reaches the
model's context? There is no single-function fix for that the way there is
for authorization -- which is itself a useful thing for students to sit
with.

## Optional extension: Legal role

If a student (or instructor demo) adds a `legal` role per the optional
extension in `labs/02-rag-security.md`:

```python
# app/core/security.py
DEMO_USERS = {
    "employee": User("alice@globaltech.example", "employee"),
    "executive": User("sarah@globaltech.example", "executive"),
    "legal": User("marcus@globaltech.example", "legal"),
}
```

```text
# data/module2_rag/documents/restricted/project_atlas.txt and board_meeting_minutes.txt
ALLOWED_ROLES: executive, legal
```

```python
def test_legal_can_retrieve_restricted_chunks(index):
    results = retrieve_documents("Tell me about Project Atlas.", "legal", index=index, top_k=10)
    restricted = [r for r in results if r.chunk.classification == "restricted"]
    assert restricted
    assert all(r.authorized for r in restricted)


def test_employee_still_cannot_retrieve_restricted_chunks_with_legal_role_present(index):
    results = retrieve_documents("Tell me about Project Atlas.", "employee", index=index, top_k=10)
    assert not [r for r in results if r.chunk.classification == "restricted"]
```

No UI changes are required -- `app/ui.py`'s identity switcher iterates
`DEMO_USERS` dynamically, exactly as in V1.
