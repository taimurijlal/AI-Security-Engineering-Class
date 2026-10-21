"""
ModuleSpec wiring for GlobalTech AI V2 — RAG Security.

Everything module-specific (retrieval, system prompt, attack categories,
reflection questions, role presentation, the RAG Inspector panel) lives
here. The shared UI shell and CLI shell drive this module through the
ModuleSpec contract alone and never import app.modules.module2_rag.rag
directly.
"""

from __future__ import annotations

import streamlit as st

from app.core.module_spec import ModuleSpec
from app.modules.module2_rag.prompts import SYSTEM_PROMPT
from app.modules.module2_rag.rag import DEFAULT_TOP_K, RetrievedChunk, build_rag_context, retrieve_documents

WELCOME_MESSAGE = (
    "Hello. I'm GlobalTech AI, the internal employee assistant. I answer "
    "questions using GlobalTech's internal knowledge base. How can I help "
    "you today?"
)

ROLE_LABELS = {
    "employee": "Standard Employee",
    "executive": "Executive",
    "contractor": "Contractor",
    "legal": "Legal",
}
ROLE_CLASSIFICATION = {
    "employee": "INTERNAL",
    "executive": "RESTRICTED",
    "contractor": "PUBLIC",
    "legal": "RESTRICTED",
}

ATTACK_CATEGORIES = [
    "Normal / Baseline",
    "Retrieval Authorization Bypass",
    "Indirect Prompt Injection via Retrieved Content",
    "Knowledge Poisoning",
    "System Prompt Disclosure",
    "Sensitive Data Disclosure",
    "Other",
]

REFLECTION_QUESTIONS = [
    "Why is semantic relevance not the same thing as authorization?",
    "Where should authorization be enforced in a RAG pipeline -- before "
    "retrieval, after retrieval, or after generation -- and why?",
    "What is the difference between a prompt injection attack and a "
    "knowledge poisoning attack, and how does each one appear in the RAG "
    "Inspector?",
    "How would you redesign retrieve_documents() so a restricted chunk can "
    "never become a retrieval candidate for an unauthorized role, instead "
    "of being retrieved and then (hopefully) not disclosed?",
    "The RAG Inspector redacts restricted chunk text for you even while "
    "you are deliberately testing as an unauthorized role. Why is that the "
    "correct behaviour for a real production tool, and not just an "
    "inconvenience for this lab?",
    "How does this vulnerability generalize to enterprise knowledge-base "
    "copilots built on top of systems like Confluence, SharePoint, or a "
    "support ticket archive?",
]


def get_context(user, prompt: str) -> tuple[str, list[RetrievedChunk]]:
    """Adapt retrieve_documents()/build_rag_context() to the ModuleSpec
    (user, prompt) -> (context, evidence) signature.

    DELIBERATE: retrieve_documents() accepts user.role but does not use it
    to filter candidates in the vulnerable starter application. The caller
    must not filter or second-guess what it returns -- see
    app/modules/module2_rag/rag.py.
    """
    retrieved = retrieve_documents(prompt, user.role, top_k=DEFAULT_TOP_K)
    context = build_rag_context(retrieved)
    return context, retrieved


def render_inspector(retrieved: list[RetrievedChunk], user_role: str) -> None:
    """Show what app/modules/module2_rag/rag.py actually retrieved for the
    preceding query.

    This panel is always visible (not gated by Security Testing Mode)
    because it is the core transparency tool this lab teaches with: it
    lets a student see a retrieval-authorization problem *before* the
    model ever responds. Restricted body text is never shown here for a
    chunk the current role is not authorized for -- that redaction holds
    in both the vulnerable and the remediated state.
    """
    with st.expander("RAG Inspector"):
        if not retrieved:
            st.caption("No chunks were retrieved for this query.")
            return

        for item in retrieved:
            chunk = item.chunk
            auth_class = "rag-authorized" if item.authorized else "rag-unauthorized"
            auth_icon = "✅" if item.authorized else "⛔"
            auth_text = "authorized" if item.authorized else "NOT authorized"
            st.markdown(
                f"{auth_icon} **{chunk.title}** &nbsp;·&nbsp; `{chunk.document_id}` "
                f"&nbsp;·&nbsp; classification: `{chunk.classification}` "
                f"&nbsp;·&nbsp; similarity: `{item.similarity:.3f}` "
                f"&nbsp;·&nbsp; <span class='{auth_class}'>{auth_text} for role `{user_role}`</span>",
                unsafe_allow_html=True,
            )
            if item.authorized:
                snippet = chunk.text[:280] + ("…" if len(chunk.text) > 280 else "")
                st.caption(snippet)
            else:
                st.caption(
                    "Body text hidden by the UI. This role is not authorized for "
                    "this classification -- but the chunk was still retrieved as "
                    "a candidate. That is the vulnerability this lab teaches."
                )
            st.markdown("---")

        st.caption(
            "This panel reflects app/modules/module2_rag/rag.py's retrieval "
            "decision, independent of what the model said in its response."
        )


def describe_evidence_cli(retrieved: list[RetrievedChunk]) -> list[str]:
    lines = []
    for item in retrieved:
        flag = "OK" if item.authorized else "UNAUTHORIZED"
        lines.append(
            f"  [retrieved] {item.chunk.document_id} "
            f"(classification={item.chunk.classification}, "
            f"similarity={item.similarity:.3f}, {flag})"
        )
    return lines


MODULE_SPEC = ModuleSpec(
    module_id="module2_rag",
    nav_label="Module 2: RAG Security",
    title="GlobalTech AI — RAG Security",
    tagline="Internal Employee Assistant — Retrieval-Augmented",
    lab_badge="RAG SECURITY ENGINEERING LAB — V2",
    welcome_message=WELCOME_MESSAGE,
    system_prompt=SYSTEM_PROMPT,
    attack_categories=ATTACK_CATEGORIES,
    reflection_questions=REFLECTION_QUESTIONS,
    export_file_name="globaltech_rag_security_assessment.md",
    get_context=get_context,
    render_inspector=render_inspector,
    describe_evidence_cli=describe_evidence_cli,
    role_labels=ROLE_LABELS,
    role_classification=ROLE_CLASSIFICATION,
)
