"""
GlobalTech AI V2 — shared data model for the RAG pipeline.

Chunk and RagIndex live in their own module, separate from app/ingestion.py,
on purpose. app/ingestion.py is meant to be run directly as a script
(`python -m app.modules.module2_rag.ingestion`), and Python's pickle module
records a class's
*module of origin* at pickle time -- if Chunk/RagIndex were defined inside
app/modules/module2_rag/ingestion.py, running it as
`python -m app.modules.module2_rag.ingestion` would bind them to the module
name `__main__` instead of `app.modules.module2_rag.ingestion`. Any other
entry point that later unpickles data/module2_rag/index/rag_index.pkl (the
Streamlit UI, the CLI,
pytest) would then fail with something like:

    AttributeError: Can't get attribute 'RagIndex' on <module '__main__' ...>

because that process's own `__main__` is a different file entirely. Keeping
these dataclasses in a module that is never executed as a script sidesteps
the problem: every entry point imports `app.modules.module2_rag.models` the
same way, so the
pickled class references always resolve correctly, no matter which file was
run to build the index or which file is running when it's loaded.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    """One retrievable unit of text plus the authorization metadata of the
    document it came from.

    classification and allowed_roles are carried on every chunk (not just
    looked up separately) so that app/rag.py and the Streamlit RAG
    Inspector can display and reason about authorization per retrieved
    chunk without a second lookup.
    """

    chunk_id: str
    document_id: str
    title: str
    source: str
    classification: str
    allowed_roles: list[str]
    chunk_index: int
    text: str


@dataclass
class RagIndex:
    """A minimal local vector index: one TF-IDF vector per chunk."""

    chunks: list[Chunk]
    vocabulary: dict[str, int]
    idf: list[float]
    chunk_vectors: list[list[float]]
