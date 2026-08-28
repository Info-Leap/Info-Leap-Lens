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

from infoleap.utils.ui_styles import inject_pulse_styles
from infoleap.db_loader import list_available_projects, get_project_meta, _DRIVE_FOLDER_MAP

inject_pulse_styles()
st.title("🗂️ Manage Projects")
st.caption(
    "Every project ingested via Add Project lives at its own `infoleap/data/<project id>/oxdata.db` "
    "— project_1 (this project's own real data) is never deletable from here."
)

with st.expander("☁️ Drive Setup for Streamlit Cloud & Remote Storage", expanded=False):
    st.markdown("""
    **To connect Google Drive storage in Streamlit Cloud:**
    1. Open your Streamlit Cloud App Settings → **Secrets**.
    2. Set `STORAGE_BACKEND = "gdrive"`.
    3. Add your GCP Service Account credentials under `[gcp_service_account]`:
    ```toml
    STORAGE_BACKEND = "gdrive"

    [gcp_service_account]
    type = "service_account"
    project_id = "your-gcp-project"
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "...@your-gcp-project.iam.gserviceaccount.com"
    client_id = "..."
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    ```
    When configured, InfoLeap automatically pulls `oxdata.db` and project files on demand.
    """)

DATA_DIR = Path(oxdata_dir) / "data"


def _respondent_count(pid: str, data_dir: Path) -> int | None:
    db_path = data_dir / pid / "oxdata.db"
    if not db_path.exists():
        db_path = data_dir / pid / "infoleap.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            n = conn.execute("SELECT COUNT(*) FROM fact_respondents").fetchone()[0]
            conn.close()
            return int(n)
        except Exception:
            pass
    # Fallback to master_mapping.xlsx row count if DB doesn't exist yet
    excel_path = data_dir / pid / "master_mapping.xlsx"
    if excel_path.exists():
        try:
            import pandas as pd
            df = pd.read_excel(excel_path, sheet_name="RAW_DATA")
            return len(df)
        except Exception:
            pass
    return None


def _table_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        tables = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fact_%'"
        ).fetchall()]
        counts = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                pass
        conn.close()
        return counts
    except Exception:
        return {}


projects = list_available_projects()
if not projects:
    st.info("No projects found under `oxdata/data/`.")
    st.stop()

active_project = st.session_state.get("active_project_id", "project_1")

st.divider()

for pid in projects:
    db_path = DATA_DIR / pid / "oxdata.db"
    if not db_path.exists():
        db_path = DATA_DIR / pid / "infoleap.db"
    excel_path = DATA_DIR / pid / "master_mapping.xlsx"
    n_resp = _respondent_count(pid, DATA_DIR)
    is_active = pid == active_project
    is_protected = pid == "project_1"
    meta = get_project_meta(pid)
    last_sync = meta.get("last_drive_sync")

    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            label = f"**{pid}**"
            if is_active:
                label += " 🟢 _active_"
            if is_protected:
                label += " 🔒"
            st.markdown(label)
            if excel_path.exists():
                st.caption(f"📊 Master Excel: `{excel_path.name}`")
            elif db_path.exists():
                st.caption(f"🗄️ Database: `{db_path.name}`")
            else:
                st.caption(f"`{DATA_DIR / pid}`")
            if last_sync:
                st.caption(f"☁️ Last Drive sync: `{last_sync}`")
        with c2:
            if n_resp is not None:
                st.metric("Respondents", f"{n_resp:,}")
            else:
                st.info("Reading...")
        with c3:
            target_file = excel_path if excel_path.exists() else db_path
            size_mb = target_file.stat().st_size / (1024 * 1024) if target_file.exists() else 0
            st.metric("File Size", f"{size_mb:.1f} MB")

        counts = _table_counts(db_path)
        if counts:
            with st.expander("Row counts per fact table"):
                for t, n in sorted(counts.items()):
                    st.markdown(f"- `{t}`: {n:,}")

        b1, b2, b3, b4 = st.columns([1.2, 1, 1, 1])
        with b1:
            if st.button("🔁 Re-ingest", key=f"reingest_{pid}"):
                st.session_state["_prefill_project_id"] = pid
                st.switch_page("views/add_project.py")
        with b2:
            if st.button("☁️ Sync to Drive", key=f"sync_drive_{pid}"):
                try:
                    from infoleap.gdrive.client import DriveClient
                    c = DriveClient()
                    if c._svc is None:
                        st.warning("Google Drive credentials not configured or unavailable.")
                    else:
                        folder_name = _DRIVE_FOLDER_MAP.get(pid, pid)
                        with st.spinner(f"Uploading files for '{pid}' to Drive..."):
                            res = c.sync_project_to_drive(folder_name, str(DATA_DIR / pid))
                        if res:
                            st.success(f"Synced {len(res)} file(s) to Drive: {', '.join(res.keys())}")
                            st.rerun()
                        else:
                            st.info("No files found to sync.")
                except Exception as e:
                    st.error(f"Drive sync failed: {e}")
        with b3:
            if st.button("⬇️ Pull Drive", key=f"pull_drive_{pid}"):
                try:
                    from infoleap.gdrive.client import DriveClient
                    c = DriveClient()
                    if c._svc is None:
                        st.warning("Google Drive credentials not configured or unavailable.")
                    else:
                        folder_name = _DRIVE_FOLDER_MAP.get(pid, pid)
                        with st.spinner(f"Pulling files for '{pid}' from Drive..."):
                            res = c.sync_project_from_drive(folder_name, str(DATA_DIR / pid))
                        if any(res.values()):
                            st.success(f"Downloaded from Drive: {', '.join(k for k, v in res.items() if v)}")
                            st.rerun()
                        else:
                            st.warning("No files found in Drive.")
                except Exception as e:
                    st.error(f"Drive pull failed: {e}")
        with b4:
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
