"""
ModuleSpec wiring for GlobalTech AI V1 — Prompt Security.

Everything module-specific (context assembly, system prompt, attack
categories, reflection questions, role presentation) lives here. The shared
UI shell and CLI shell drive this module through the ModuleSpec contract
alone and never import app.modules.module1_prompt_security.context directly.
"""

from __future__ import annotations

from app.core.module_spec import ModuleSpec
from app.modules.module1_prompt_security.context import get_context as _get_context
from app.modules.module1_prompt_security.prompts import SYSTEM_PROMPT

WELCOME_MESSAGE = (
    "Hello. I'm GlobalTech AI, the internal employee assistant. "
    "How can I help you today?"
)

ROLE_LABELS = {
    "employee": "Standard Employee",
    "executive": "Executive",
    "contractor": "Contractor",
}
ROLE_CLASSIFICATION = {
    "employee": "INTERNAL",
    "executive": "RESTRICTED",
    "contractor": "PUBLIC",
}

ATTACK_CATEGORIES = [
    "Normal / Baseline",
    "Direct Prompt Injection",
    "System Prompt Disclosure",
    "Sensitive Data Disclosure",
    "Instruction Manipulation",
    "Other",
]

REFLECTION_QUESTIONS = [
    "Which attacks depended on probabilistic model behaviour?",
    "Which security controls should exist outside the LLM?",
    "What sensitive information was available to the model?",
    "What changed after authorization was implemented?",
]


def get_context(user, prompt: str) -> tuple[str, None]:
    """Adapt app.modules.module1_prompt_security.context.get_context() to the
    ModuleSpec (user, prompt) -> (context, evidence) signature.

    DELIBERATE: this module has no retrieval step, so there is no evidence
    to carry -- it always returns None.
    """
    return _get_context(user.role), None


MODULE_SPEC = ModuleSpec(
    module_id="module1_prompt_security",
    nav_label="Module 1: Prompt Security",
    title="GlobalTech AI — Prompt Security",
    tagline="Internal Employee Assistant",
    lab_badge="AI SECURITY ENGINEERING LAB — V1",
    welcome_message=WELCOME_MESSAGE,
    system_prompt=SYSTEM_PROMPT,
    attack_categories=ATTACK_CATEGORIES,
    reflection_questions=REFLECTION_QUESTIONS,
    export_file_name="globaltech_prompt_security_assessment.md",
    get_context=get_context,
    render_inspector=None,
    describe_evidence_cli=None,
    role_labels=ROLE_LABELS,
    role_classification=ROLE_CLASSIFICATION,
)
