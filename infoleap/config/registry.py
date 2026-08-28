"""
Project Registry
================
Central lookup for all projects.

To add a new project:
  1. Create config/project_N.py with the new mappings
  2. Import it here and add to PROJECTS dict
  3. Run:  python etl/load_data.py --project project_N

The active project for the Streamlit app is set via ACTIVE_PROJECT.
"""

from config.project_1 import PROJECT_ID as P1_ID
import config.project_1 as project_1

# Registry: project_id → config module
PROJECTS = {
    "project_1": project_1,
    # "project_2": project_2,   ← add future projects here
}

# Default project loaded by the app and ETL when no --project flag is given
ACTIVE_PROJECT = "project_1"


def get_project(project_id: str = None):
    """Return the config module for a project id. Defaults to ACTIVE_PROJECT."""
    pid = project_id or ACTIVE_PROJECT
    if pid not in PROJECTS:
        raise ValueError(
            f"Unknown project '{pid}'. "
            f"Available: {list(PROJECTS.keys())}"
        )
    return PROJECTS[pid]


def db_path(project_id: str = None) -> str:
    """Return the SQLite DB path for a given project."""
    from pathlib import Path
    pid   = project_id or ACTIVE_PROJECT
    base  = Path(__file__).parent.parent
    return str(base / "data" / pid / "oxdata.db")


def list_projects() -> list[dict]:
    """Return a summary list of all registered projects."""
    return [
        {"project_id": pid, "name": cfg.PROJECT_NAME}
        for pid, cfg in PROJECTS.items()
    ]
