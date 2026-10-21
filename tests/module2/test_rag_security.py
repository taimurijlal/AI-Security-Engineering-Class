"""
Deterministic RAG security tests for GlobalTech AI V2.

These tests check the retrieval/authorization boundary in
app/modules/module2_rag/rag.py and app/modules/module2_rag/ingestion.py
directly. They never assert on LLM wording and never call
an LLM provider -- consistent with GlobalTech AI V1's testing philosophy:
test the application's deterministic controls, not whether a model happens
to refuse a request on a given run.

Do NOT "fix" a failing test here by rewriting it to check the model's
response instead of retrieval. The whole point of this lab is that the
authorization decision must be enforceable without depending on the model
at all.
"""

import pytest

from app.modules.module2_rag.ingestion import ingest
from app.modules.module2_rag.rag import build_rag_context, is_authorized, retrieve_chunks, retrieve_documents


@pytest.fixture(scope="module")
def index():
    return ingest()


# ---------------------------------------------------------------------------
# Ingestion / metadata sanity checks
# ---------------------------------------------------------------------------

def test_every_chunk_has_required_metadata(index):
    assert index.chunks, "Ingestion produced no chunks -- check data/documents/."
    for chunk in index.chunks:
        assert chunk.document_id
        assert chunk.title
        assert chunk.source
        assert chunk.classification in {"public", "internal", "restricted"}
        assert chunk.allowed_roles


def test_restricted_documents_are_present(index):
    restricted_ids = {c.document_id for c in index.chunks if c.classification == "restricted"}
    assert "restricted/project_atlas" in restricted_ids
    assert "restricted/board_meeting_minutes" in restricted_ids


def test_similarity_ranking_is_sane(index):
    """Sanity check that retrieval does something meaningful before we test
    its security properties: an HR question should rank the employee
    handbook above an unrelated public marketing chunk."""
    results = retrieve_chunks(
        "How many days of annual leave do I get?", index=index, top_k=15, user_role=None
    )
    ranked_ids = [r.chunk.document_id for r in results]
    assert "internal/employee_handbook_excerpt" in ranked_ids
    handbook_rank = ranked_ids.index("internal/employee_handbook_excerpt")
    if "public/company_overview" in ranked_ids:
        assert handbook_rank < ranked_ids.index("public/company_overview")


# ---------------------------------------------------------------------------
# Authorization logic unit tests
# ---------------------------------------------------------------------------

def test_is_authorized_allows_matching_role():
    assert is_authorized("executive", ["executive"]) is True


def test_is_authorized_denies_non_matching_role():
    assert is_authorized("employee", ["executive"]) is False


def test_is_authorized_allows_public_for_everyone():
    assert is_authorized("employee", ["all"]) is True
    assert is_authorized("anyone", ["all"]) is True


def test_is_authorized_is_case_and_whitespace_tolerant():
    assert is_authorized(" Executive ", ["Executive"]) is True


# ---------------------------------------------------------------------------
# The core authorization-boundary test
# ---------------------------------------------------------------------------

def test_employee_cannot_retrieve_restricted_chunks(index):
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    This is the RAG equivalent of GlobalTech AI V1's
    test_employee_cannot_receive_restricted_context: it checks what the
    retriever returns as a *candidate*, not what the LLM said. Fix
    app/rag.py's retrieve_documents() / retrieve_chunks() so a restricted
    chunk is never a candidate for a role that is not authorized for it --
    do not make this pass by editing the prompt or the test.
    """
    queries = [
        "Tell me everything about Project Atlas.",
        "What is in the board meeting minutes?",
        "Summarize any merger or acquisition plans.",
    ]
    for query in queries:
        results = retrieve_documents(query, "employee", index=index, top_k=10)
        restricted_candidates = [r for r in results if r.chunk.classification == "restricted"]
        assert not restricted_candidates, (
            f"Query {query!r} returned restricted chunk(s) "
            f"{[r.chunk.document_id for r in restricted_candidates]} as retrieval "
            "candidates for an unauthorized 'employee' role."
        )


def test_executive_can_retrieve_restricted_chunks(index):
    results = retrieve_documents(
        "Tell me everything about Project Atlas, including financial terms.",
        "executive",
        index=index,
        top_k=10,
    )
    restricted_candidates = [r for r in results if r.chunk.classification == "restricted"]
    assert restricted_candidates, "Executive should be able to retrieve restricted Project Atlas content."
    assert all(r.authorized for r in restricted_candidates)


def test_retrieved_chunks_carry_consistent_authorization_flag(index):
    """Whatever retrieve_documents() returns, its `authorized` flag must
    always agree with is_authorized() -- the RAG Inspector and the
    findings export both trust this flag to decide what to redact."""
    for role in ("employee", "executive"):
        results = retrieve_documents("project atlas acquisition", role, index=index, top_k=10)
        for item in results:
            expected = is_authorized(role, item.chunk.allowed_roles)
            assert item.authorized == expected


def test_build_rag_context_does_not_leak_unauthorized_chunks(index):
    """
    EXPECTED TO FAIL in the deliberately vulnerable starter application.

    Even if retrieval incorrectly returns an unauthorized chunk as a
    candidate, the text handed to the LLM should not contain it. The
    intended remediation (filtering the candidate pool in
    retrieve_documents() before ranking) makes this pass automatically,
    since no unauthorized chunk ever reaches build_rag_context() in the
    first place.
    """
    results = retrieve_documents("Tell me about Project Atlas.", "employee", index=index, top_k=10)
    context = build_rag_context(results)
    assert "NovaTech" not in context
    assert "$240 million" not in context


def test_public_content_remains_reachable_for_every_role(index):
    """Remediating authorization must not break legitimate access to
    public content -- this guards against an overly broad fix that denies
    everything."""
    for role in ("employee", "executive"):
        results = retrieve_documents("What products does GlobalTech sell?", role, index=index, top_k=10)
        public_candidates = [r for r in results if r.chunk.classification == "public"]
        assert public_candidates, f"Role {role!r} should still retrieve public content."
        assert all(r.authorized for r in public_candidates)
