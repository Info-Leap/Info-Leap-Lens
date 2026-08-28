"""
OxData - External Database Loader (Optimized for LENS 3.0)
========================================================
Ensures the database is found regardless of where the script is run from.

Project-aware (2026-07-27, Phase 3 of multi-project ingestion — see
.planning/MULTIPROJECT_INGESTION_LOG_2026-07-27.md): pass project_id explicitly, or omit it and
this reads st.session_state["active_project_id"] when running inside Streamlit. Every existing
call site in the codebase calls get_db_path() with no arguments — those are UNCHANGED: no
active_project_id in session_state (or not running under Streamlit at all, e.g. a CLI script)
means project_id defaults to "project_1", producing the exact same search_paths as before this
change. Nothing needs to be touched at any of the ~30 existing call sites for this to be additive.

Drive backend (2026-08-27): set STORAGE_BACKEND=gdrive in .env to pull oxdata.db from Google Drive
instead of local data/. DB is cached in oxdata/data/{project_id}/oxdata.db after first download.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

# ── Drive project folder name mapping ─────────────────────────────────────────
# Maps project_id → Drive folder name under Quantitative/
_DRIVE_FOLDER_MAP: dict[str, str] = {
    "project_1":    "project_1__elec_appliances",
    "akshayakalpa": "akshayakalpa__dairy",
}


def _drive_download_db(project_id: str, dest: Path) -> bool:
    """Download oxdata.db from Drive to dest. Returns True on success."""
    try:
        import sys
        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from infoleap.gdrive.client import DriveClient
        client = DriveClient()
        folder_name = _DRIVE_FOLDER_MAP.get(project_id, project_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        return client.download_db(folder_name, str(dest))
    except Exception as e:
        print(f"[db_loader] Drive download failed for {project_id}: {e}")
        return False


def list_available_projects() -> list[str]:
    """Project ids available — from Drive (if STORAGE_BACKEND=gdrive) or local data/."""
    if os.environ.get("STORAGE_BACKEND", "local") == "gdrive":
        try:
            import sys
            repo_root = Path(__file__).resolve().parent.parent
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            from infoleap.gdrive.client import DriveClient
            client = DriveClient()
            folders = client.list_quant_projects()
            # Map Drive folder names back to project_ids (reverse of _DRIVE_FOLDER_MAP)
            reverse = {v: k for k, v in _DRIVE_FOLDER_MAP.items()}
            return sorted(reverse.get(f, f) for f in folders)
        except Exception:
            pass  # fall through to local
    data_dir = Path(__file__).resolve().parent / "data"
    if not data_dir.exists():
        return []
    return sorted(
        p.name for p in data_dir.iterdir()
        if p.is_dir() and (
            (p / "infoleap.db").exists() or
            (p / "master_mapping.xlsx").exists() or
            (p / "raw_data.xlsx").exists() or
            (p / "project_meta.json").exists()
        )
    )


def sync_from_drive_if_needed(project_id: str) -> bool:
    """
    If STORAGE_BACKEND=gdrive env var is set AND local project folder is missing 
    master_mapping.xlsx AND oxdata.db, try to download from Drive.
    Returns True if synced, False otherwise.
    Called by get_db_path() before checking local files.
    """
    if os.environ.get("STORAGE_BACKEND", "local") != "gdrive":
        return False
    oxdata_dir = Path(__file__).resolve().parent
    local_dir = oxdata_dir / "data" / project_id
    if (local_dir / "master_mapping.xlsx").exists() or (local_dir / "infoleap.db").exists():
        return False
    try:
        import sys
        repo_root = oxdata_dir.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from infoleap.gdrive.client import DriveClient
        client = DriveClient()
        folder_name = _DRIVE_FOLDER_MAP.get(project_id, project_id)
        local_dir.mkdir(parents=True, exist_ok=True)
        res = client.sync_project_from_drive(folder_name, str(local_dir))
        return any(res.values())
    except Exception as e:
        print(f"[db_loader] sync_from_drive_if_needed failed for {project_id}: {e}")
        return False


def get_db_path(required_table: str = "fact_respondents", project_id: Optional[str] = None) -> Path:
    """Finds the database file in known locations. Prioritizes the larger root database.

    When STORAGE_BACKEND=gdrive: downloads DB from Drive on first call (or if local cache missing),
    then returns the local cache path. Subsequent calls serve from cache without re-downloading.
    """
    if project_id is None:
        project_id = "project_1"
        try:
            import streamlit as st
            project_id = st.session_state.get("active_project_id", "project_1")
        except Exception:
            pass  # not running under Streamlit (CLI script, test) — stays "project_1"

    oxdata_dir = Path(__file__).resolve().parent
    project_root = oxdata_dir.parent

    # ── Drive backend: pull files if not cached locally ────────────────────────
    sync_from_drive_if_needed(project_id)
    if os.environ.get("STORAGE_BACKEND", "local") == "gdrive":
        local_cache = oxdata_dir / "data" / project_id / "infoleap.db"
        if not local_cache.exists():
            print(f"[db_loader] Downloading {project_id}/oxdata.db from Drive…")
            _drive_download_db(project_id, local_cache)
        if local_cache.exists():
            return local_cache

    search_paths = [
        # 1. Package Root (canonical production DB — newest schema with BQ3/funnel data)
        oxdata_dir / "data" / project_id / "infoleap.db",
        # 2. Project Root (legacy location — older schema, kept as fallback)
        project_root / "data" / project_id / "infoleap.db",
    ]
    if project_id == "project_1":
        search_paths += [
            # 3. Lens Database
            project_root / "lens" / "lens.db",
            # 4. Root fallback
            project_root / "lens.db",
        ]

    for p in search_paths:
        if p.exists():
            # Quick check if it has the critical tables we need
            try:
                conn = sqlite3.connect(str(p))
                tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                conn.close()
                if required_table in tables:
                    return p
            except:
                continue
                
    # Fallback to first existing if no specific table match found
    for p in search_paths:
        if p.exists():
            return p
                
    raise FileNotFoundError(f"No database found for project '{project_id}'")

_PROJECT_META_DEFAULTS = {
    "display_name": "",
    "industry": "Brand Intelligence",
    "description": "",
    "n_respondents": 0,
    "wave": "",
    "nps_industry_avg": 45,
    "has_category_dimension": False,
    "category_names": [],
    "attribute_themes": ["All"],
}


def get_project_meta(project_id: Optional[str] = None) -> dict:
    """Load per-project metadata from project_meta.json beside the DB.

    Falls back to _PROJECT_META_DEFAULTS for any missing key so callers can
    always do get_project_meta()["nps_industry_avg"] without KeyError.
    Returns defaults (with project_id filled in) if no file is found.
    """
    if project_id is None:
        project_id = "project_1"
        try:
            import streamlit as st
            project_id = st.session_state.get("active_project_id", "project_1")
        except Exception:
            pass

    oxdata_dir = Path(__file__).resolve().parent
    meta_path = oxdata_dir / "data" / project_id / "project_meta.json"
    meta = dict(_PROJECT_META_DEFAULTS)
    meta["project_id"] = project_id
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                loaded = json.load(f)
            meta.update(loaded)
        except Exception:
            pass
    if not meta.get("display_name"):
        meta["display_name"] = project_id
    return meta


if __name__ == "__main__":
    path = get_db_path()
    if path:
        print(f"✅ Database found at: {path}")
    else:
        print("❌ Database NOT FOUND in search paths.")
