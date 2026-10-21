"""
GlobalTech AI V2 — retrieval-augmented generation pipeline.

Two central lessons this module exists to teach:

1. "Relevance is not authorization."
   A semantic search ranks chunks by how well they match the query.
   Nothing about that ranking asks whether the requesting user is allowed
   to see the result. Those are two different questions, and this starter
   application only asks the first one.

2. "Retrieved content is untrusted input."
   Whatever text retrieval returns gets concatenated into the model's
   context and sent to the LLM as part of its prompt. If a retrieved
   document contains instructions aimed at the model (see
   data/module2_rag/documents/internal/quarterly_business_update.txt), the
   model may
   follow them -- exactly as it might follow instructions a user typed
   directly. Retrieved text is not automatically trustworthy just because
   it came from internal storage instead of a chat box.

DELIBERATE VULNERABILITY (starter state):
retrieve_chunks() ranks every chunk in the index by similarity to the
query, with no regard for user_role. retrieve_documents() and
build_rag_context() pass every retrieved chunk's full text into the
model's context -- including restricted chunks -- whenever they are
semantically relevant to the query, for any role. is_authorized() exists
and is even attached to every retrieved chunk for display purposes, but
nothing in this module *enforces* it before content reaches the model.

Students remediate this during the lab by making authorization constrain
*retrieval itself* -- filtering the candidate pool before ranking, not
filtering (or trusting the LLM to filter) after the fact. See
instructor/SOLUTION.md for the reference fix. Do not "fix" this by adding
another sentence to the system prompt -- that repeats GlobalTech AI V1's
mistake with extra steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.modules.module2_rag.ingestion import embed_query, ingest, load_index
from app.modules.module2_rag.models import Chunk, RagIndex

DEFAULT_TOP_K = 4

_cached_index: RagIndex | None = None


@dataclass
class RetrievedChunk:
    """A single ranked retrieval result plus its authorization status.

    `authorized` is computed for observability (the Streamlit RAG
    Inspector uses it to show students what *should* have happened) --
    it is not used by retrieve_chunks()/retrieve_documents() to decide
    what to return in the starter application.
    """

    chunk: Chunk
    similarity: float
    authorized: bool


def is_authorized(user_role: str, allowed_roles: list[str]) -> bool:
    """The application's one and only authorization decision for RAG content.

    A chunk is authorized for a role if that role appears in its
    allowed_roles list, or if the chunk is marked "all" (public content).
    This function is deterministic and has nothing to do with the LLM --
    it plays exactly the role app/context.py's role check played for
    static context in GlobalTech AI V1.
    """
    role = (user_role or "").strip().lower()
    normalized = {entry.strip().lower() for entry in allowed_roles}
    return "all" in normalized or role in normalized


def get_index(force_rebuild: bool = False) -> RagIndex:
    """Return the in-memory RAG index, loading or building it if needed.

    Order of preference: in-memory cache -> index file on disk -> build a
    fresh index from data/module2_rag/documents/. This means `streamlit run
    app/ui.py` and `python -m app.main` both work even if a student forgets
    to run `python -m app.modules.module2_rag.ingestion` first, while that is
    still the documented, explicit way to (re)build the index after
    editing the document corpus.
    """
    global _cached_index

    if force_rebuild:
        _cached_index = ingest()
        return _cached_index

    if _cached_index is not None:
        return _cached_index

    try:
        _cached_index = load_index()
    except FileNotFoundError:
        _cached_index = ingest()
    return _cached_index


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve_chunks(
    query: str,
    index: RagIndex | None = None,
    top_k: int = DEFAULT_TOP_K,
    user_role: str | None = None,
) -> list[RetrievedChunk]:
    """Rank every chunk in the index by similarity to the query.

    DELIBERATE VULNERABILITY: user_role is accepted (so the call site and
    the RAG Inspector can show which role asked) but it is NOT used to
    filter or re-rank candidates. Every chunk in the corpus -- public,
    internal, or restricted -- is eligible to be returned for any role, as
    long as it is semantically relevant to the query.
    """
    if index is None:
        index = get_index()

    query_vector = embed_query(query, index)

    scored = [
        (chunk, _cosine_similarity(query_vector, vector))
        for chunk, vector in zip(index.chunks, index.chunk_vectors)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    results: list[RetrievedChunk] = []
    for chunk, similarity in scored[:top_k]:
        authorized = is_authorized(user_role, chunk.allowed_roles) if user_role is not None else True
        results.append(RetrievedChunk(chunk=chunk, similarity=similarity, authorized=authorized))
    return results


def retrieve_documents(
    query: str,
    user_role: str,
    index: RagIndex | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    """Retrieve chunks for a specific user session.

    DELIBERATE VULNERABILITY: this simply forwards to retrieve_chunks() and
    returns whatever comes back, authorized or not. A secure version of
    this function would restrict the *candidate pool* to chunks where
    is_authorized(user_role, chunk.allowed_roles) is True before ranking by
    similarity -- so an unauthorized chunk is never a retrieval candidate
    in the first place, rather than being retrieved and then (hopefully)
    not disclosed by the model. That is the fix: authorization constrains
    retrieval, it does not filter retrieval's output after the fact.
    """
    return retrieve_chunks(query, index=index, top_k=top_k, user_role=user_role)


def build_rag_context(retrieved: list[RetrievedChunk]) -> str:
    """Concatenate retrieved chunk text into a context block for the LLM.

    DELIBERATE VULNERABILITY: every retrieved chunk's full text is included
    here, regardless of its `authorized` flag. Even if a restricted chunk
    is correctly flagged unauthorized by is_authorized(), its content still
    reaches the model's context -- and therefore the model itself -- in
    this starter application. This mirrors GlobalTech AI V1's
    get_context(), which sent RESTRICTED_CONTEXT to the model for every
    role and relied on the system prompt alone to prevent disclosure.
    """
    if not retrieved:
        return "No relevant company documents were found for this question."

    blocks = [
        f"[Source: {item.chunk.title} ({item.chunk.source}), "
        f"classification={item.chunk.classification}]\n{item.chunk.text}"
        for item in retrieved
    ]
    return "\n\n".join(blocks)
