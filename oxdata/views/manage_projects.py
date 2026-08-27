"""
Manage Projects — list existing ingested projects, show respondent counts, delete a project's
folder, and jump to Add Project for re-ingestion into an existing project id.

Re-uploading into an existing project id already works via oxdata/views/add_project.py (its
ingest path uses CREATE TABLE IF NOT EXISTS / DROP VIEW+CREATE VIEW, so typing an existing
project id there appends rather than errors) — this page's "Re-ingest" button just pre-fills
that id via session_state so the user doesn't have to remember/retype it. See
.planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md for the full pipeline history.
"""

import shutil
import sys
import os
from pathlib import Path

import sqlite3
import streamlit as st

current_dir = os.path.dirname(os.path.abspath(__file__))
oxdata_dir = os.path.dirname(current_dir)
repo_root = os.path.dirname(oxdata_dir)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from oxdata.utils.ui_styles import inject_pulse_styles
from oxdata.db_loader import list_available_projects

inject_pulse_styles()
st.title("🗂️ Manage Projects")
st.caption(
    "Every project ingested via Add Project lives at its own `oxdata/data/<project id>/oxdata.db` "
    "— project_1 (this project's own real data) is never deletable from here."
)

DATA_DIR = Path(oxdata_dir) / "data"


def _respondent_count(db_path: Path) -> int | None:
    try:
        conn = sqlite3.connect(str(db_path))
        n = conn.execute("SELECT COUNT(*) FROM fact_respondents").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return None


def _table_counts(db_path: Path) -> dict[str, int]:
    counts = {}
    try:
        conn = sqlite3.connect(str(db_path))
        tables = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fact_%'"
        ).fetchall()]
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return counts


projects = list_available_projects()
if not projects:
    st.info("No projects found under `oxdata/data/`.")
    st.stop()

active_project = st.session_state.get("active_project_id", "project_1")

st.divider()

for pid in projects:
    db_path = DATA_DIR / pid / "oxdata.db"
    n_resp = _respondent_count(db_path)
    is_active = pid == active_project
    is_protected = pid == "project_1"

    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            label = f"**{pid}**"
            if is_active:
                label += " 🟢 _active_"
            if is_protected:
                label += " 🔒"
            st.markdown(label)
            st.caption(f"`{db_path}`")
        with c2:
            if n_resp is not None:
                st.metric("Respondents", f"{n_resp:,}")
            else:
                st.warning("Couldn't read fact_respondents")
        with c3:
            size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0
            st.metric("DB size", f"{size_mb:.1f} MB")

        with st.expander("Row counts per fact table"):
            counts = _table_counts(db_path)
            if counts:
                for t, n in sorted(counts.items()):
                    st.markdown(f"- `{t}`: {n:,}")
            else:
                st.caption("No fact_* tables readable.")

        b1, b2 = st.columns(2)
        with b1:
            if st.button("🔁 Re-ingest into this project", key=f"reingest_{pid}"):
                st.session_state["_prefill_project_id"] = pid
                st.switch_page("views/add_project.py")
        with b2:
            if is_protected:
                st.button("🗑️ Delete", key=f"del_{pid}", disabled=True,
                           help="project_1 is the real production database — never deletable here.")
            else:
                confirm_key = f"confirm_del_{pid}"
                if st.session_state.get(confirm_key):
                    st.warning(f"Really delete `{pid}`? This removes the folder permanently.")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("✅ Yes, delete", key=f"del_yes_{pid}", type="primary"):
                            try:
                                shutil.rmtree(DATA_DIR / pid)
                                # 2026-07-30: do NOT write st.session_state["active_project_id"]
                                # here — that key is bound to the sidebar selectbox widget in
                                # app.py, which already instantiated earlier in this same script
                                # run (the sidebar renders before every page body). Streamlit
                                # raises "cannot be modified after the widget... is instantiated"
                                # if you assign to a widget-bound key mid-run. app.py's own
                                # fallback (`if _current_project not in _available_projects:
                                # _current_project = _available_projects[0]`) already self-heals
                                # on the next run once the deleted project drops out of
                                # list_available_projects() — nothing else needed here.
                                st.session_state.pop(confirm_key, None)
                                st.cache_data.clear()
                                st.success(f"Deleted `{pid}`.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")
                    with cc2:
                        if st.button("Cancel", key=f"del_cancel_{pid}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                else:
                    if st.button("🗑️ Delete", key=f"del_{pid}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
