"""Guards against the registry.json data_paths drifting out of sync with the
filesystem again. ethnographic_renderer.py:1085 reads proj["data_paths"]["matrices"]
directly (not the ProjectManager-computed abs_paths) — if this string is wrong,
the Mixer page silently shows "0 extracted / 0 total" with real data on disk.
"""
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY = _ROOT / "data" / "projects" / "registry.json"


def test_all_data_paths_resolve_to_existing_files_or_dirs():
    registry = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    missing = []
    for proj in registry["projects"]:
        for key, rel_path in proj.get("data_paths", {}).items():
            full_path = _ROOT / rel_path
            if not full_path.exists():
                missing.append(f"{proj['id']}.data_paths.{key} = {rel_path!r} -> {full_path}")
    assert not missing, "Stale registry.json data_paths (file/dir doesn't exist):\n" + "\n".join(missing)


def test_mixer_matrices_path_has_data_prefix():
    registry = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    mixer = next(p for p in registry["projects"] if p["id"] == "mixer")
    assert mixer["data_paths"]["matrices"].startswith("data/"), (
        "Mixer matrices path must start with 'data/' — ethnographic_renderer.py "
        "resolves it as base_path / data_paths['matrices'], and base_path is oxdata/, "
        "not oxdata/data/"
    )
