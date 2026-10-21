"""
The contract every lab module implements so the shared UI shell
(app/core/ui_shell.py), CLI shell (app/core/cli_shell.py) and registry
(app/registry.py) can drive it without knowing its internals.

Adding a new module means writing one of these and registering it in
app/registry.py -- nothing in app/core/ needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.core.security import User


@dataclass
class ModuleSpec:
    module_id: str
    nav_label: str
    title: str
    tagline: str
    lab_badge: str
    welcome_message: str
    system_prompt: str
    attack_categories: list[str]
    reflection_questions: list[str]
    export_file_name: str

    # (user, prompt) -> (context_text_for_the_llm, evidence)
    # `evidence` is whatever extra, module-specific data the module wants to
    # carry alongside the response (e.g. RAG retrieval results). Pass None
    # for modules with nothing to show beyond the chat reply itself.
    get_context: Callable[[object, str], tuple[str, object | None]]

    # Streamlit-only: (evidence, user_role) -> renders an expander panel
    # under the response (e.g. the RAG Inspector). None if not applicable.
    render_inspector: Callable[[object, str], None] | None = None

    # CLI-only: evidence -> lines to print after the response. None if not
    # applicable.
    describe_evidence_cli: Callable[[object], list[str]] | None = None

    role_labels: dict[str, str] = field(default_factory=dict)
    role_classification: dict[str, str] = field(default_factory=dict)

    # --- Optional extension points below. All default to "off", meaning a
    # module that doesn't set them (Module 1, Module 2) behaves exactly as
    # before. These exist for Module 3 (MCP Security), which needs a
    # multi-step tool-call loop, extra tabs, and its own identity roster.

    # Streamlit-only: overrides the shared shell's default single-call
    # handle_new_prompt() flow. (spec, prompt) -> None; the callable is
    # responsible for appending to chat history / session state itself.
    # None means "use the shared shell's default flow", same as today.
    handle_prompt: Callable[["ModuleSpec", str], None] | None = None

    # Streamlit-only: overrides the shared shell's default render_chat()
    # entirely (message history + inspector + finding form + chat_input).
    # Needed by modules whose chat surface needs extra inline UI the shared
    # loop has no hook for (e.g. Module 3's write-action approve/deny
    # prompt, which must appear inline under a specific message). None
    # means "use the shared shell's default render_chat()", same as today.
    render_chat: Callable[["ModuleSpec"], None] | None = None

    # Streamlit-only: additional (label, render_fn) tabs appended after
    # Chat / Security Assessment. render_fn takes the spec and renders into
    # the already-active tab. Empty list (the default) means no extra tabs
    # and the shell's existing two-tab/one-tab logic is unchanged.
    extra_tabs: list[tuple[str, Callable[["ModuleSpec"], None]]] = field(default_factory=list)

    # Optional free-text note the shell can render (e.g. an architecture
    # explainer in the sidebar or an info box). None means nothing extra is
    # shown, same as today.
    architecture_note: str | None = None

    # Optional per-module override of the global DEMO_USERS roster, keyed by
    # a person/identity key (e.g. "alice", "bob") rather than by role, so a
    # module can offer more than one identity per role. None (the default)
    # means "fall back to app.core.security.DEMO_USERS", i.e. today's
    # behavior for Module 1 and Module 2.
    demo_users: dict[str, "User"] | None = None

    # Optional override of the shared shell's generic
    # findings.build_markdown_report() used by the "Export Assessment"
    # button. (findings, spec, tester_email, tester_role, provider_name,
    # model_name) -> markdown report text. None (the default) means "use
    # the shared generic report", i.e. today's behavior for Modules 1-3.
    # Needed by Module 4 (Agent Security), whose assessment report follows
    # a bespoke multi-section structure the generic per-finding report
    # cannot express.
    custom_export_fn: (
        Callable[[list, "ModuleSpec", str, str, str, str], str] | None
    ) = None
