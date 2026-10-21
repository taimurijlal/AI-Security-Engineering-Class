"""
Shared Streamlit rendering shell for every lab module.

Each module supplies a ModuleSpec (app/core/module_spec.py). This file knows
how to turn a spec into a working chat UI, Security Testing Mode, and
Security Assessment tab -- the mechanics every module shares. Module-specific
behaviour (what context the model receives, what the inspector panel shows)
stays in the module's own package and is injected through the spec.

Conversation state is namespaced per module (st.session_state keys are
prefixed with the module_id) so switching modules in the sidebar does not
lose or mix up another module's conversation. The LLM provider/connection
status is process-global since it depends only on .env, not on which module
is active.
"""

from __future__ import annotations

import streamlit as st

from app.core.audit import record_interaction
from app.core.findings import (
    ASSESSMENT_OPTIONS,
    build_markdown_report,
    clear_findings,
    load_findings,
    record_finding,
    summarize,
    write_markdown_report,
)
from app.core.llm import LLMError, OllamaProvider, get_provider
from app.core.module_spec import ModuleSpec
from app.core.security import DEMO_USERS

CUSTOM_CSS = """
<style>
.stApp { background-color: #fafbfc; }
section[data-testid="stSidebar"] {
    background-color: #f4f6f8;
    border-right: 1px solid #e2e6ea;
}
.lab-badge {
    display: inline-block;
    background-color: #eef1f5;
    color: #33475b;
    border: 1px solid #d5dbe3;
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    margin-bottom: 0.75rem;
}
.role-badge {
    display: inline-block;
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.03em;
}
.role-employee { background-color: #e6f0ff; color: #1a4fa3; }
.role-executive { background-color: #fdeaea; color: #a32828; }
.role-contractor { background-color: #eef1f5; color: #33475b; }
.role-legal { background-color: #fdeaea; color: #a32828; }
.role-hr { background-color: #fff4e5; color: #8a5a00; }
.role-admin { background-color: #f3e8ff; color: #5b21b6; }
.rag-authorized { color: #1a7a3c; font-weight: 600; }
.rag-unauthorized { color: #a32828; font-weight: 600; }
</style>
"""


def _key(spec: ModuleSpec, name: str) -> str:
    return f"{spec.module_id}:{name}"


def _demo_users(spec: ModuleSpec) -> dict:
    """Resolve the identity roster for this module: its own override if it
    set one (Module 3's multi-identity roster), otherwise the shared global
    roster every other module uses today."""
    return spec.demo_users or DEMO_USERS


def init_session_state(spec: ModuleSpec) -> None:
    if _key(spec, "user_key") not in st.session_state:
        st.session_state[_key(spec, "user_key")] = next(iter(_demo_users(spec)))
    if _key(spec, "messages") not in st.session_state:
        st.session_state[_key(spec, "messages")] = [
            {"role": "assistant", "content": spec.welcome_message}
        ]
    if _key(spec, "evidence") not in st.session_state:
        st.session_state[_key(spec, "evidence")] = {}
    if _key(spec, "security_mode") not in st.session_state:
        st.session_state[_key(spec, "security_mode")] = False
    if _key(spec, "setup_error") not in st.session_state:
        st.session_state[_key(spec, "setup_error")] = None
    if _key(spec, "confirm_clear_findings") not in st.session_state:
        st.session_state[_key(spec, "confirm_clear_findings")] = False

    if "provider" not in st.session_state:
        try:
            st.session_state.provider = get_provider()
            st.session_state.provider_error = None
        except LLMError as exc:
            st.session_state.provider = None
            st.session_state.provider_error = str(exc)

    if "connection_status" not in st.session_state:
        st.session_state.connection_status = check_connection()


def check_connection() -> tuple:
    provider = st.session_state.get("provider")
    if provider is None:
        return False, st.session_state.get("provider_error", "LLM provider is not configured.")
    try:
        return provider.health_check()
    except Exception as exc:  # defensive: never let a health check crash the UI
        return False, f"Health check failed: {exc}"


def reset_conversation(spec: ModuleSpec) -> None:
    st.session_state[_key(spec, "messages")] = [
        {"role": "assistant", "content": spec.welcome_message}
    ]
    st.session_state[_key(spec, "evidence")] = {}
    st.session_state[_key(spec, "setup_error")] = None


def switch_identity(spec: ModuleSpec, user_key: str) -> None:
    if user_key != st.session_state[_key(spec, "user_key")]:
        st.session_state[_key(spec, "user_key")] = user_key
        reset_conversation(spec)


def _on_identity_radio_change(spec: ModuleSpec, user_keys: list[str], labels: list[str]) -> None:
    selected_label = st.session_state[_key(spec, "identity_radio")]
    switch_identity(spec, user_keys[labels.index(selected_label)])


def _on_security_mode_toggle_change(spec: ModuleSpec) -> None:
    st.session_state[_key(spec, "security_mode")] = st.session_state[_key(spec, "security_mode_widget")]


def friendly_setup_error(raw_message: str) -> str:
    return (
        "GlobalTech AI could not start because the LLM provider is not configured.\n\n"
        f"Details: {raw_message}\n\n"
        "Check your .env file against .env.example."
    )


def friendly_llm_error(provider, exc: LLMError) -> str:
    if isinstance(provider, OllamaProvider):
        checklist = (
            "Check that:\n"
            "1. Ollama is running\n"
            "2. the configured model has been downloaded\n"
            "3. your .env configuration is correct"
        )
        suggestion = f"\n\nSuggested command:\n`ollama pull {provider.model}`"
    else:
        checklist = (
            "Check that:\n"
            "1. OPENAI_API_KEY is set in .env\n"
            "2. OPENAI_MODEL is set in .env\n"
            "3. OPENAI_BASE_URL (if set) points to a reachable endpoint"
        )
        suggestion = ""

    return f"Unable to reach the configured LLM.\n\n{checklist}{suggestion}"


def handle_new_prompt(spec: ModuleSpec, prompt: str) -> None:
    user = _demo_users(spec)[st.session_state[_key(spec, "user_key")]]
    messages_key = _key(spec, "messages")
    st.session_state[messages_key].append({"role": "user", "content": prompt})
    st.session_state[_key(spec, "setup_error")] = None

    provider = st.session_state.provider
    if provider is None:
        st.session_state[_key(spec, "setup_error")] = friendly_setup_error(
            st.session_state.provider_error
        )
        return

    # DELIBERATE: spec.get_context() is whatever the module's vulnerable (or
    # fixed) retrieval/context logic returns. The UI must not filter or
    # second-guess it.
    context, evidence = spec.get_context(user, prompt)

    try:
        with st.spinner("GlobalTech AI is thinking..."):
            response = provider.generate(
                system_prompt=spec.system_prompt,
                user_prompt=prompt,
                context=context,
            )
    except LLMError as exc:
        st.session_state[_key(spec, "setup_error")] = friendly_llm_error(provider, exc)
        return

    st.session_state[messages_key].append({"role": "assistant", "content": response})
    assistant_idx = len(st.session_state[messages_key]) - 1
    st.session_state[_key(spec, "evidence")][assistant_idx] = evidence
    record_interaction(user, spec.module_id, prompt, response)


def render_role_badge(spec: ModuleSpec, role: str) -> None:
    css_class = (
        f"role-badge role-{role}" if role in spec.role_classification else "role-badge role-contractor"
    )
    label = spec.role_labels.get(role, role.title())
    st.markdown(f"<span class='{css_class}'>{label.upper()}</span>", unsafe_allow_html=True)


def render_sidebar(spec: ModuleSpec) -> None:
    with st.sidebar:
        demo_users = _demo_users(spec)
        user = demo_users[st.session_state[_key(spec, "user_key")]]
        st.markdown("**SESSION**")
        st.write(f"User: `{user.email}`")
        render_role_badge(spec, user.role)
        st.write(f"Max clearance: {spec.role_classification.get(user.role, 'UNKNOWN')}")

        provider = st.session_state.provider
        model_name = provider.model if provider else "unavailable"
        provider_name = provider.PROVIDER_NAME if provider else "unconfigured"
        st.write(f"Model: {model_name} ({provider_name})")

        connected, detail = st.session_state.connection_status
        status_text = "Connected" if connected else "Model unavailable"
        status_icon = "🟢" if connected else "⚪"
        st.write(f"Status: {status_icon} {status_text}")
        with st.expander("Connection details"):
            st.caption(detail)
        if st.button("Recheck connection", use_container_width=True):
            st.session_state.connection_status = check_connection()
            st.rerun()

        st.divider()
        st.markdown("**IDENTITY**")
        user_keys = list(demo_users.keys())
        labels = [spec.role_labels.get(key, key.title()) for key in user_keys]
        current_index = user_keys.index(st.session_state[_key(spec, "user_key")])
        st.radio(
            "Switch identity",
            labels,
            index=current_index,
            label_visibility="collapsed",
            key=_key(spec, "identity_radio"),
            on_change=_on_identity_radio_change,
            args=(spec, user_keys, labels),
        )
        st.caption("Switching identity starts a new conversation.")

        st.divider()
        st.toggle(
            "Security Testing Mode",
            value=st.session_state[_key(spec, "security_mode")],
            key=_key(spec, "security_mode_widget"),
            on_change=_on_security_mode_toggle_change,
            args=(spec,),
        )
        st.caption("When on, you can record a finding under any AI response.")

        st.divider()
        if st.button("Reset Conversation", use_container_width=True, key=_key(spec, "reset_btn")):
            reset_conversation(spec)
            st.rerun()

        confirm_key = _key(spec, "confirm_clear_findings")
        if st.session_state[confirm_key]:
            st.warning(f"This permanently deletes recorded findings for {spec.title}.")
            col_confirm, col_cancel = st.columns(2)
            if col_confirm.button("Confirm Clear", use_container_width=True, key=_key(spec, "confirm_btn")):
                clear_findings(spec.module_id)
                st.session_state[confirm_key] = False
                st.rerun()
            if col_cancel.button("Cancel", use_container_width=True, key=_key(spec, "cancel_btn")):
                st.session_state[confirm_key] = False
                st.rerun()
        else:
            if st.button("Clear Security Findings", use_container_width=True, key=_key(spec, "clear_btn")):
                st.session_state[confirm_key] = True
                st.rerun()


def render_finding_form(spec: ModuleSpec, idx: int, user, prompt_text: str, response_text: str, evidence) -> None:
    with st.expander("Security Testing"):
        with st.form(key=_key(spec, f"finding_form_{idx}")):
            category = st.selectbox("Attack category", spec.attack_categories, key=_key(spec, f"cat_{idx}"))
            assessment = st.selectbox("Assessment", ASSESSMENT_OPTIONS, key=_key(spec, f"assess_{idx}"))
            impact = st.text_area("Observed security impact", key=_key(spec, f"impact_{idx}"))
            notes = st.text_area("Tester notes (optional)", key=_key(spec, f"notes_{idx}"))
            submitted = st.form_submit_button("Record Finding")

        if submitted:
            record_finding(
                user,
                spec.module_id,
                category,
                prompt_text,
                response_text,
                assessment,
                impact,
                notes,
                retrieved_chunks=evidence,
            )
            st.success("Finding recorded.")


def render_chat(spec: ModuleSpec) -> None:
    setup_error = st.session_state[_key(spec, "setup_error")]
    if setup_error:
        st.error(setup_error)

    user = _demo_users(spec)[st.session_state[_key(spec, "user_key")]]
    messages = st.session_state[_key(spec, "messages")]
    evidence_by_idx = st.session_state[_key(spec, "evidence")]

    for idx, message in enumerate(messages):
        with st.chat_message(message["role"]):
            st.write(message["content"])
        # idx 0 is always the welcome message, not a real model exchange.
        if message["role"] == "assistant" and idx > 0:
            evidence = evidence_by_idx.get(idx)
            if spec.render_inspector is not None:
                spec.render_inspector(evidence or [], user.role)
            if st.session_state[_key(spec, "security_mode")]:
                prompt_text = messages[idx - 1]["content"]
                render_finding_form(spec, idx, user, prompt_text, message["content"], evidence)

    prompt = st.chat_input("Message GlobalTech AI...", key=_key(spec, "chat_input"))
    if prompt:
        (spec.handle_prompt or handle_new_prompt)(spec, prompt)
        st.rerun()


def render_security_assessment_tab(spec: ModuleSpec) -> None:
    findings = load_findings(spec.module_id)
    summary = summarize(findings)
    show_retrieval_column = spec.render_inspector is not None
    show_tool_column = any(finding.get("mcp_evidence", {}).get("tool_calls") for finding in findings)

    st.subheader("Security Assessment")
    st.caption(f"Findings recorded for {spec.title} in Security Testing Mode.")

    cols = st.columns(4 + int(show_retrieval_column) + int(show_tool_column))
    cols[0].metric("Tests Recorded", summary["total"])
    cols[1].metric("Blocked", summary["Blocked"])
    cols[2].metric("Partially Successful", summary["Partially Successful"])
    cols[3].metric("Successful", summary["Successful"])
    next_col = 4
    if show_retrieval_column:
        cols[next_col].metric("Unauthorized Retrieval", summary["unauthorized_retrieval_observed"])
        next_col += 1
    if show_tool_column:
        cols[next_col].metric("Unauthorized Tool Execution", summary["unauthorized_tool_call_observed"])
        next_col += 1

    st.divider()

    if not findings:
        st.info(
            "No findings recorded yet. Enable Security Testing Mode, send a "
            "test prompt in the Chat tab, then use 'Security Testing' under "
            "the AI response to record what you observed."
        )
    else:
        for finding in reversed(findings):
            flag = " ⚠️ unauthorized retrieval" if finding.get("unauthorized_retrieval_observed") else ""
            flag += " ⚠️ unauthorized tool execution" if finding.get("unauthorized_tool_call_observed") else ""
            title = f"{finding.get('attack_category', 'Unknown')} — {finding.get('assessment', 'Unrated')}{flag}"
            with st.expander(title):
                st.caption(finding.get("timestamp", ""))
                rag_evidence = finding.get("rag_evidence", {}).get("retrieved", [])
                if rag_evidence:
                    st.markdown(f"**Query used**\n\n{finding.get('query_used', '')}")
                    st.markdown("**Retrieved evidence (metadata only)**")
                    st.table(rag_evidence)
                else:
                    st.markdown(f"**Prompt**\n\n{finding.get('prompt', '')}")
                mcp_evidence = finding.get("mcp_evidence", {}).get("tool_calls", [])
                if mcp_evidence:
                    st.markdown("**MCP tool-call evidence (result redacted when not authorized)**")
                    st.table(
                        [
                            {
                                "tool_name": call.get("tool_name", ""),
                                "executed": call.get("executed", ""),
                                "expected_authorization": call.get("expected_authorization", ""),
                                "policy_reason": call.get("policy_reason", ""),
                            }
                            for call in mcp_evidence
                        ]
                    )
                st.markdown(f"**Model response**\n\n{finding.get('model_response', '')}")
                st.markdown(f"**Observed security impact**\n\n{finding.get('security_impact') or '_none recorded_'}")
                if finding.get("tester_notes"):
                    st.markdown(f"**Tester notes**\n\n{finding.get('tester_notes')}")

    st.divider()
    if st.button("Export Assessment", key=_key(spec, "export_btn")):
        user = _demo_users(spec)[st.session_state[_key(spec, "user_key")]]
        provider = st.session_state.provider
        provider_name = provider.PROVIDER_NAME if provider else "unconfigured"
        model_name = provider.model if provider else "unavailable"
        if spec.custom_export_fn is not None:
            report = spec.custom_export_fn(
                findings,
                spec,
                user.email,
                user.role,
                provider_name,
                model_name,
            )
        else:
            report = build_markdown_report(
                findings,
                spec.title,
                user.email,
                user.role,
                provider_name,
                model_name,
                spec.reflection_questions,
            )
        write_markdown_report(report, spec.export_file_name)
        st.success(f"Assessment exported to {spec.export_file_name}")
        st.download_button(
            "Download report",
            report,
            file_name=spec.export_file_name,
            mime="text/markdown",
            key=_key(spec, "download_btn"),
        )


def render_module(spec: ModuleSpec) -> None:
    """Render one module's full page: init state, title, chat, and the
    Security Assessment tab when Security Testing Mode is on."""
    init_session_state(spec)
    render_sidebar(spec)

    st.title("GlobalTech AI")
    st.caption(spec.tagline)
    st.markdown(f"<span class='lab-badge'>{spec.lab_badge}</span>", unsafe_allow_html=True)

    if spec.architecture_note:
        with st.expander("Architecture note", expanded=False):
            st.markdown(spec.architecture_note)

    security_mode = st.session_state[_key(spec, "security_mode")]
    tab_labels = ["Chat"]
    if security_mode:
        tab_labels.append("Security Assessment")
    tab_labels.extend(label for label, _render in spec.extra_tabs)

    if len(tab_labels) == 1:
        # No Security Testing Mode and no extra tabs: identical to today's
        # single-tab (no st.tabs() at all) behaviour.
        (spec.render_chat or render_chat)(spec)
        return

    tabs = st.tabs(tab_labels)
    tabs_iter = iter(tabs)

    with next(tabs_iter):
        (spec.render_chat or render_chat)(spec)

    if security_mode:
        with next(tabs_iter):
            render_security_assessment_tab(spec)

    for (_label, render_fn), tab in zip(spec.extra_tabs, tabs_iter):
        with tab:
            render_fn(spec)
