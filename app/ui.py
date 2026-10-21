"""
GlobalTech AI — unified Streamlit browser UI.

This is a thin entry point: it renders the module switcher in the sidebar
and hands off to app.core.ui_shell.render_module() for everything else. All
module-specific logic (context, retrieval, system prompt, inspector panel)
lives in each module's own module.py -- this file never imports from a
specific module's internals.

Run with:
    streamlit run app/ui.py
"""

import sys
from pathlib import Path

# When Streamlit runs this file directly, it puts this file's own directory
# (app/) on sys.path, not the project root -- so a plain `import app.registry`
# would fail with "No module named 'app'". Adding the project root (this
# file's parent's parent) makes the `app` package importable no matter how
# this script is launched.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.core.ui_shell import CUSTOM_CSS, render_module
from app.registry import MODULES, MODULES_BY_ID

st.set_page_config(page_title="GlobalTech AI", layout="wide")


def main() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    if "active_module_id" not in st.session_state:
        st.session_state.active_module_id = MODULES[0].module_id

    with st.sidebar:
        st.markdown("### GLOBALTECH AI")
        st.caption("AI Security Engineering Labs")
        st.divider()
        st.markdown("**MODULE**")
        labels = [spec.nav_label for spec in MODULES]
        current_index = [spec.module_id for spec in MODULES].index(st.session_state.active_module_id)
        selected_label = st.radio(
            "Switch module",
            labels,
            index=current_index,
            label_visibility="collapsed",
        )
        st.session_state.active_module_id = MODULES[labels.index(selected_label)].module_id
        st.divider()

    active_spec = MODULES_BY_ID[st.session_state.active_module_id]
    render_module(active_spec)


if __name__ == "__main__":
    main()
