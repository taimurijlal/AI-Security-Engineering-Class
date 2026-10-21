"""
GlobalTech AI V2 — document ingestion for the RAG pipeline.

Pipeline: load .txt files under data/module2_rag/documents/ -> parse a small metadata
header -> split each document into paragraph-sized chunks -> attach
metadata to every chunk -> build a local TF-IDF vector index -> persist the
index to disk so app/rag.py can load it without re-embedding on every run.

Deliberately lightweight by design:
- No paid embedding API. Everything runs fully offline.
- No GPU and no multi-gigabyte model download.
- No managed or cloud vector database.
- Uses a small, hand-written TF-IDF + cosine similarity implementation
  built entirely from the Python standard library. Nothing beyond what
  is already in requirements.txt needs to be installed for this module.

This is intentionally NOT a production-grade retrieval system. It is just
good enough to demonstrate a real semantic-search RAG pipeline for the
security lesson this lab teaches: relevance ranking has nothing to do with
authorization. A more sophisticated embedding model would make retrieval
*better*, not *safer* -- the vulnerability this lab teaches lives in
app/rag.py, not in the quality of the similarity search.

Run directly to (re)build the index:

    python -m app.modules.module2_rag.ingestion
"""

from __future__ import annotations

import math
import pickle
import re
from pathlib import Path

from app.modules.module2_rag.models import Chunk, RagIndex

DOCUMENTS_DIR = Path("data/module2_rag/documents")
INDEX_FILE = Path("data/module2_rag/index/rag_index.pkl")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _parse_document(path: Path) -> tuple[dict, str]:
    """Split a .txt file into its metadata header and body text.

    Header format:

        TITLE: Human readable title
        CLASSIFICATION: public | internal | restricted
        ALLOWED_ROLES: role_a, role_b
        ---
        <body text, one or more paragraphs>
    """
    raw = path.read_text(encoding="utf-8")
    if "---" not in raw:
        raise ValueError(f"{path} is missing the '---' header delimiter.")

    header_text, body = raw.split("---", 1)
    metadata: dict[str, str] = {}
    for line in header_text.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().upper()] = value.strip()

    allowed_roles_raw = metadata.get("ALLOWED_ROLES", "")
    allowed_roles = [role.strip().lower() for role in allowed_roles_raw.split(",") if role.strip()]

    parsed = {
        "title": metadata.get("TITLE", path.stem),
        "classification": metadata.get("CLASSIFICATION", "internal").lower(),
        "allowed_roles": allowed_roles or ["all"],
    }
    return parsed, body.strip()


def _chunk_text(body: str) -> list[str]:
    """Split body text into paragraph-sized chunks.

    Paragraphs are separated by a blank line. This keeps chunks short,
    keeps each chunk's provenance easy to display in the RAG Inspector, and
    is easy for students to read and reason about without a tokenizer.
    """
    return [paragraph.strip() for paragraph in re.split(r"\n\s*\n", body) if paragraph.strip()]


def load_documents(documents_dir: Path = DOCUMENTS_DIR) -> list[Chunk]:
    """Load every .txt file under documents_dir and split it into chunks.

    Each chunk carries the full metadata of its parent document
    (document_id, title, source, classification, allowed_roles) plus its
    own chunk_index.
    """
    chunks: list[Chunk] = []
    for path in sorted(documents_dir.rglob("*.txt")):
        relative = path.relative_to(documents_dir)
        document_id = str(relative.with_suffix("")).replace("\\", "/")
        metadata, body = _parse_document(path)

        for index, paragraph in enumerate(_chunk_text(body)):
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}::{index}",
                    document_id=document_id,
                    title=metadata["title"],
                    source=str(relative).replace("\\", "/"),
                    classification=metadata["classification"],
                    allowed_roles=metadata["allowed_roles"],
                    chunk_index=index,
                    text=paragraph,
                )
            )
    return chunks


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _build_vocabulary(token_lists: list[list[str]]) -> dict[str, int]:
    vocabulary: dict[str, int] = {}
    for tokens in token_lists:
        for token in tokens:
            if token not in vocabulary:
                vocabulary[token] = len(vocabulary)
    return vocabulary


def _term_frequencies(tokens: list[str], vocabulary: dict[str, int]) -> list[float]:
    vector = [0.0] * len(vocabulary)
    if not tokens:
        return vector
    for token in tokens:
        idx = vocabulary.get(token)
        if idx is not None:
            vector[idx] += 1.0
    length = len(tokens)
    return [count / length for count in vector]


def _inverse_document_frequencies(token_lists: list[list[str]], vocabulary: dict[str, int]) -> list[float]:
    doc_count = len(token_lists)
    doc_freq = [0] * len(vocabulary)
    for tokens in token_lists:
        for token in set(tokens):
            idx = vocabulary.get(token)
            if idx is not None:
                doc_freq[idx] += 1
    # Smoothed IDF so every term gets a finite, usable weight.
    return [math.log((1 + doc_count) / (1 + df)) + 1.0 for df in doc_freq]


def embed_query(query: str, index: RagIndex) -> list[float]:
    """Project a free-text query into the same TF-IDF space as the index.

    This is the "embedding model" for this lab: deterministic, offline,
    and small enough to read top to bottom in one sitting.
    """
    tokens = _tokenize(query)
    tf = _term_frequencies(tokens, index.vocabulary)
    return [tf_value * idf_value for tf_value, idf_value in zip(tf, index.idf)]


def build_index(chunks: list[Chunk] | None = None) -> RagIndex:
    """Build a local TF-IDF index over the given (or freshly loaded) chunks.

    This plays the role a real embedding model + vector store would play in
    a production RAG pipeline, without a model download, a GPU, or a paid
    API. TF-IDF vectors compared with cosine similarity are enough to
    demonstrate the lesson this lab teaches -- semantic relevance ranking
    says nothing about authorization -- without adding heavyweight ML
    infrastructure to a security course.
    """
    if chunks is None:
        chunks = load_documents()

    token_lists = [_tokenize(chunk.text) for chunk in chunks]
    vocabulary = _build_vocabulary(token_lists)
    idf = _inverse_document_frequencies(token_lists, vocabulary)

    chunk_vectors = [
        [tf_value * idf_value for tf_value, idf_value in zip(_term_frequencies(tokens, vocabulary), idf)]
        for tokens in token_lists
    ]

    return RagIndex(chunks=chunks, vocabulary=vocabulary, idf=idf, chunk_vectors=chunk_vectors)


def save_index(index: RagIndex, path: Path = INDEX_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(index, handle)
    return path


def load_index(path: Path = INDEX_FILE) -> RagIndex:
    if not path.exists():
        raise FileNotFoundError(f"No RAG index found at {path}. Run: python -m app.modules.module2_rag.ingestion")
    with path.open("rb") as handle:
        return pickle.load(handle)


def ingest(documents_dir: Path = DOCUMENTS_DIR, index_path: Path = INDEX_FILE) -> RagIndex:
    """End-to-end pipeline: load -> chunk -> embed -> index -> persist."""
    chunks = load_documents(documents_dir)
    index = build_index(chunks)
    save_index(index, index_path)
    return index


def main() -> None:
    index = ingest()
    print(f"Indexed {len(index.chunks)} chunk(s) from {DOCUMENTS_DIR}/")

    by_classification: dict[str, int] = {}
    for chunk in index.chunks:
        by_classification[chunk.classification] = by_classification.get(chunk.classification, 0) + 1
    for classification, count in sorted(by_classification.items()):
        print(f"  {classification}: {count} chunk(s)")

    print(f"Index written to {INDEX_FILE}")


if __name__ == "__main__":
    main()
