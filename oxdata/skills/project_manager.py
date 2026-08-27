import subprocess
import json
import sys
import os
import argparse
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

class ProjectManager:
    def __init__(self, registry_path: Path = None):
        self._path = registry_path or _DATA_DIR / "projects" / "registry.json"
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        if not self._path.exists():
            return {"projects": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"projects": []}

    def list_projects(self) -> list[dict]:
        return self._registry.get("projects", [])

    def get_project(self, project_id: str) -> dict:
        for p in self.list_projects():
            if p["id"] == project_id:
                # Add absolute paths for internal use (copy — must not mutate
                # the registry entry with non-JSON-serializable Path objects,
                # or a later _save_registry() call silently fails/corrupts).
                p = dict(p)
                base = _DATA_DIR / "projects" / project_id
                p["abs_paths"] = {
                    "transcripts": base / "transcripts",
                    "matrices": base / "matrices",
                    "source_docs": base / "source_docs",
                    "findings": base / "findings",
                    "schema": base / "schema" / "extraction_schema.json",
                    "ui_config": base / "schema" / "ui_config.json"
                }
                return p
        return {}

    def get_status(self, project_id: str) -> str:
        """Returns the processing status of a project."""
        p = self.get_project(project_id)
        return p.get("status", "raw")

    def _save_registry(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._registry, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _update_registry_entry(self, project_id: str, **fields) -> None:
        """Writes fields (e.g. status, last_processed) into the registry entry
        for project_id and persists to disk. trigger_processing previously ran
        the full subprocess chain without ever recording the outcome, so
        registry.json's status/last_processed silently went stale (confirmed
        wrong for karat-coindcx: 115 matrices on disk, registry said null)."""
        for entry in self._registry.get("projects", []):
            if entry.get("id") == project_id:
                entry.update(fields)
                self._save_registry()
                return

    def trigger_processing(self, project_id: str, full: bool = False) -> dict:
        """
        Triggers the full pipeline for a project.
        """
        p = self.get_project(project_id)
        if not p:
            return {"ok": False, "error": f"Project {project_id} not found"}

        results = {"project_id": project_id, "steps": []}
        scripts_dir = Path(__file__).parent

        def _run(step_name: str, script_name: str, timeout: int = 3600) -> bool:
            try:
                script = scripts_dir / script_name
                result = subprocess.run(
                    [sys.executable, str(script), "--project", project_id],
                    capture_output=True, text=True, timeout=timeout,
                    cwd=str(_DATA_DIR.parent.parent)
                )
                results["steps"].append({
                    "step": step_name,
                    "ok": result.returncode == 0,
                    "output": result.stdout[-500:] if result.stdout else "",
                    "error": result.stderr[-300:] if result.stderr else "",
                })
                return result.returncode == 0
            except Exception as e:
                results["steps"].append({"step": step_name, "ok": False, "error": str(e)})
                return False

        # Step 1: Schema generation
        schema = p["abs_paths"].get("schema")
        if not schema or not schema.exists() or full:
            ok = _run("schema_generation", "schema_generator.py", timeout=300)
            if not ok and not full: return results # only block if not full or specifically failed

        # Step 2: docx -> md
        if p.get("transcript_format") == "docx":
            ok = _run("docx_to_md", "docx_to_md.py", timeout=120)

        # Step 3: Extraction
        _run("extraction", "project_extractor.py", timeout=3600)

        # Step 4: Verification
        _run("verbatim_verification", "verify_verbatims.py", timeout=600)

        # Step 5: Findings generation
        _run("findings_generation", "findings_generator.py", timeout=1800)

        results["ok"] = all(s["ok"] for s in results["steps"] if s["step"] != "schema_generation" or full)

        if results["ok"]:
            import datetime
            self._update_registry_entry(
                project_id, status="processed",
                last_processed=str(datetime.date.today()),
            )
        else:
            self._update_registry_entry(project_id, status="error")

        return results

    def scan_for_new_projects(self) -> list[str]:
        """Discover project dirs not yet in registry.json, create project.json if
        missing, and register each one — mirrors the ZIP-upload registration path
        (quote_explorer.py's Install Project button) so folder-drop onboarding
        actually surfaces in the UI instead of sitting invisible on disk."""
        projects_dir = _DATA_DIR / "projects"
        known_ids = {p["id"] for p in self._registry.get("projects", [])}
        new_ids = []
        if not projects_dir.exists(): return []
        registry_changed = False
        for d in projects_dir.iterdir():
            if not d.is_dir() or d.name in known_ids or d.name.startswith("."):
                continue
            project_json = d / "project.json"
            if not project_json.exists():
                transcripts_dir = d / "transcripts"
                has_docx = transcripts_dir.exists() and any(transcripts_dir.glob("*.docx"))
                has_md   = transcripts_dir.exists() and any(transcripts_dir.glob("*.md"))
                if has_docx or has_md:
                    fmt = "docx" if has_docx else "md"
                    minimal = {
                        "id": d.name,
                        "display_name": d.name.replace("_", " ").replace("-", " ").title(),
                        "transcript_format": fmt,
                        "status": "raw",
                        "data_paths": {
                            "transcripts": f"projects/{d.name}/transcripts",
                            "matrices":    f"projects/{d.name}/matrices",
                            "source_docs": f"projects/{d.name}/source_docs",
                            "schema":      f"projects/{d.name}/schema/extraction_schema.json",
                        },
                        "created_at": str(__import__("datetime").date.today()),
                    }
                    try:
                        project_json.write_text(json.dumps(minimal, indent=2), encoding="utf-8")
                    except Exception: continue
            if project_json.exists():
                try:
                    entry = json.loads(project_json.read_text(encoding="utf-8"))
                except Exception:
                    continue
                entry.setdefault("id", d.name)
                self._registry.setdefault("projects", []).append(entry)
                known_ids.add(d.name)
                registry_changed = True
                new_ids.append(d.name)
        if registry_changed:
            self._save_registry()
        return new_ids

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InfoLeap Project Manager CLI")
    parser.add_argument("--project", type=str, help="Project ID")
    parser.add_argument("--full", action="store_true", help="Run full pipeline")
    parser.add_argument("--scan", action="store_true", help="Scan for new projects")
    args = parser.parse_args()

    pm = ProjectManager()
    if args.scan:
        new_ids = pm.scan_for_new_projects()
        print(f"Found {len(new_ids)} new projects: {', '.join(new_ids)}")
    elif args.project:
        if args.full:
            print(f"Triggering full pipeline for {args.project}...")
            res = pm.trigger_processing(args.project, full=True)
            print(json.dumps(res, indent=2))
        else:
            print(f"Project metadata for {args.project}:")
            print(json.dumps(pm.get_project(args.project), indent=2, default=str))
    else:
        parser.print_help()
