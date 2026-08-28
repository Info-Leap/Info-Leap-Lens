"""
InfoLeap Pulse — Entry point.
Defines navigation and global page config.
All page logic lives in views/.
"""

import os
import sys

import streamlit as st

# Ensure project root in sys.path so infoleap package resolves correctly
_current_file = os.path.abspath(__file__)
_current_dir = os.path.dirname(_current_file)
_project_root = os.path.dirname(_current_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _current_dir not in sys.path:
    sys.path.append(_current_dir)

from infoleap.utils.context import ContextEngine
ContextEngine.init()

# Auth gate — set INFOLEAP_AUTH_ENABLED=1 in production to require Firebase login.
if os.environ.get("INFOLEAP_AUTH_ENABLED", "0") == "1":
    from infoleap.auth.firebase_auth import require_auth
    _current_user = require_auth()
    if "user" not in st.session_state:
        st.session_state["user"] = _current_user

from infoleap.utils.ui_styles import inject_pulse_styles

st.set_page_config(
    page_title="InfoLeap Pulse",
    page_icon="🟢",
    layout="wide",
)

inject_pulse_styles()

from infoleap.components.jump_menu import show_jump_menu

# Sidebar Branding
st.sidebar.markdown("""
    <div class="sidebar-brand">
        <div class="brand-circle">P</div>
        <div style="font-size: 1.25rem; font-weight: 700; color: white;">InfoLeap Pulse</div>
    </div>
""", unsafe_allow_html=True)

if st.sidebar.button("🔍 Jump to... (⌘K)", use_container_width=True):
    show_jump_menu()

# Quant project switcher — only shown when >1 project exists
from infoleap.db_loader import list_available_projects
_available_projects = list_available_projects()
if len(_available_projects) > 1:
    _current_project = st.session_state.get("active_project_id", "project_1")
    if _current_project not in _available_projects:
        _current_project = _available_projects[0]
    # Default to "project_1" literal (not _current_project) to avoid self-referential
    # comparison on first switch of each session — see comment history for full context.
    _prev_project = st.session_state.get("_last_active_project_id", "project_1")
    _selected_project = st.sidebar.selectbox(
        "🔎 Active Project", _available_projects,
        index=_available_projects.index(_current_project),
        key="active_project_id",
        help="Switches which project's database Brand Health, Ask Pulse, and other quant pages read from.",
    )
    if _selected_project != _prev_project:
        # Cache must be cleared on switch — get_db_path() is not a cache argument,
        # so Streamlit's cache key doesn't change when the active project does.
        st.cache_data.clear()
        st.session_state["_last_active_project_id"] = _selected_project
        st.rerun()

pg = st.navigation(
    {
        "Workspace": [
            st.Page("views/dashboard.py",      title="Home",            icon="🏠", default=True),
            st.Page("views/brand_health.py",    title="Brand Health",    icon="📈"),
            st.Page("views/quote_explorer.py",  title="Quote Explorer",  icon="🗣️"),
        ],
        "Data": [
            st.Page("views/repository.py",      title="Repository",      icon="📚"),
            st.Page("views/formula_reference.py", title="Formulas",      icon="🧪"),
            st.Page("views/add_project.py",     title="Add Project",     icon="➕"),
            st.Page("views/manage_projects.py", title="Manage Projects", icon="🗂️"),
            st.Page("views/settings.py",        title="Settings",        icon="⚙️"),
        ],
    }
)

pg.run()

# Sidebar Footer
_user = st.session_state.get("user")
if _user:
    st.sidebar.markdown(f"""
        <div class="sidebar-footer">
            <div style="font-weight: 600; margin-bottom: 2px;">{_user.get('name', '')}</div>
            <div style="opacity: 0.7; font-size: 0.75rem;">{_user.get('email', '')}</div>
        </div>
    """, unsafe_allow_html=True)
    if st.sidebar.button("Sign out", use_container_width=True):
        del st.session_state["user"]
        st.rerun()
else:
    st.sidebar.markdown("""
        <div class="sidebar-footer">
            Press ⌘K anywhere to jump between screens.
        </div>
    """, unsafe_allow_html=True)
