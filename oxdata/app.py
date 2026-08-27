"""
OxData — Entry point.
Defines navigation and global page config.
All page logic lives in views/chat.py and views/schema.py.
"""

import streamlit as st
import os
import sys

# --- 0. GLOBAL PATH INJECTION (LENS 3.2 STABILITY) ---
# Auth gate: set INFOLEAP_AUTH_ENABLED=1 in production to require Firebase login.
# Leave unset (or =0) for local dev — skips login entirely.
# Ensure the project root is in sys.path so we can import 'oxdata' as a package
import os
import sys
current_file = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file)
project_root = os.path.dirname(current_dir)

if project_root not in sys.path:
    sys.path.insert(0, project_root)
if current_dir not in sys.path:
    sys.path.append(current_dir)

try:
    import oxdata
except KeyError:
    import types
    pkg = types.ModuleType("oxdata")
    pkg.__path__ = [current_dir]
    sys.modules["oxdata"] = pkg
    import oxdata
except Exception:
    pass
from oxdata.utils.context import ContextEngine
# Initialize Unified Context Engine
ContextEngine.init()

# ── Auth gate ─────────────────────────────────────────────────────────────────
if os.environ.get("INFOLEAP_AUTH_ENABLED", "0") == "1":
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from auth.firebase_auth import require_auth
    _current_user = require_auth()
    # Expose to all pages via session state
    if "user" not in st.session_state:
        st.session_state["user"] = _current_user

from oxdata.utils.ui_styles import inject_pulse_styles

st.set_page_config(
    page_title="InfoLeap Pulse",
    page_icon="🟢",
    layout="wide",
)

# Inject Global Pulse Styles
inject_pulse_styles()

from oxdata.components.jump_menu import show_jump_menu

# Sidebar Branding (Custom HTML)
st.sidebar.markdown("""
    <div class="sidebar-brand">
        <div class="brand-circle">P</div>
        <div style="font-size: 1.25rem; font-weight: 700; color: white;">InfoLeap Pulse</div>
    </div>
""", unsafe_allow_html=True)

if st.sidebar.button("🔍 Jump to... (⌘K)", use_container_width=True):
    show_jump_menu()

# ── Quant project switcher (2026-07-27, Phase 3 of multi-project ingestion) ─────────────────
# Only shown once >1 project actually exists — a single-project install (the common case today)
# gets no extra UI. Selecting a project writes active_project_id, which get_db_path() (in
# oxdata/db_loader.py) reads for every call site that doesn't pass project_id explicitly — no
# other file needed to change for pages to follow the switch.
from oxdata.db_loader import list_available_projects
_available_projects = list_available_projects()
if len(_available_projects) > 1:
    _current_project = st.session_state.get("active_project_id", "project_1")
    if _current_project not in _available_projects:
        _current_project = _available_projects[0]
    # 2026-07-28, found live via browser-testing a fresh session's FIRST project switch: this
    # must NOT default to `_current_project`. Streamlit applies a widget's new value to
    # session_state BEFORE the script reruns, so by the time this line runs after a selectbox
    # change, `_current_project` (read from session_state["active_project_id"] above) already
    # equals the NEW selection, not the prior one. On a brand-new session (before
    # "_last_active_project_id" has ever been set), that made this fallback self-referential —
    # `_prev_project` silently took on the just-selected value too, so `_selected_project !=
    # _prev_project` came back False on the FIRST switch of every session, skipping the cache
    # clear below entirely: the sidebar label updated but every page kept rendering the OLD
    # project's cached data with no error, no warning, nothing to suggest anything was wrong.
    # Defaulting to the literal "project_1" instead breaks that self-reference; the only cost is
    # one harmless extra cache-clear+rerun on cold start if a session was somehow seeded with a
    # non-default project before this ever ran (cache-clear is idempotent and cheap).
    _prev_project = st.session_state.get("_last_active_project_id", "project_1")
    _selected_project = st.sidebar.selectbox(
        "📁 Active Project", _available_projects,
        index=_available_projects.index(_current_project),
        key="active_project_id",
        help="Switches which project's database Brand Health, Ask Pulse, and other quant "
             "pages read from.",
    )
    if _selected_project != _prev_project:
        # 2026-07-27: found live-testing the switch — every @st.cache_data-wrapped data function
        # across the app resolves its DB path internally via get_db_path(), which isn't one of
        # the function's actual arguments, so Streamlit's cache key doesn't change when the
        # active project does. Without this, switching projects updates the sidebar label but
        # every page keeps showing the PREVIOUS project's cached data until each cache entry
        # separately expires. Clearing on every actual switch (not every rerun — _prev_project
        # guards that) is blunt but correct; scoping it to only quant-data caches would need
        # threading project_id through every @st.cache_data signature in the app instead.
        st.cache_data.clear()
        st.session_state["_last_active_project_id"] = _selected_project
        st.rerun()

pg = st.navigation(
    {
        "Workspace": [
            st.Page("views/dashboard.py",     title="Home",            icon="🏠", default=True),
            st.Page("views/brand_health.py",  title="Brand Health",    icon="📈"),
            st.Page("views/quote_explorer.py", title="Quote Explorer", icon="🗣️"),
        ],
        "Data": [
            st.Page("views/repository.py",    title="Repository",      icon="📚"),
            st.Page("views/formula_reference.py", title="Formulas",    icon="🧪"),
            st.Page("views/add_project.py",   title="Add Project",     icon="➕"),
            st.Page("views/manage_projects.py", title="Manage Projects", icon="🗂️"),
            st.Page("views/settings.py",      title="Settings",        icon="⚙️"),
        ],
    }
)

# Render Navigation
pg.run()

# Sidebar Footer Context
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
