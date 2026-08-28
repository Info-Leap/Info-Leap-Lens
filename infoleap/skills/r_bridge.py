"""
r_bridge.py — Python → Rscript subprocess bridge for statistical analysis.

Usage:
    from infoleap.skills.r_bridge import run_r_stat, r_available

    if r_available():
        result = run_r_stat("cronbach_alpha", df)  # df is a pandas DataFrame

Architecture:
    Python writes CSV to temp file → Rscript.exe runs script → reads JSON output.
    No rpy2 dependency — works reliably on Windows via subprocess.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd

# R scripts directory
_SCRIPTS_DIR = Path(__file__).parent.parent / "r_scripts"
_R_EXECUTABLE = r"C:\Program Files\R\R-4.6.0\bin\Rscript.exe"

_SCRIPT_MAP = {
    "cronbach_alpha":  "cronbach_alpha.R",
    "driver_regression": "driver_regression.R",
    "logistic_regression": "logistic_regression.R",
    "anova_analysis":  "anova_analysis.R",
    "factor_analysis": "factor_analysis.R",
    "random_forest":   "random_forest.R",
}


def r_available() -> bool:
    """Check if Rscript.exe is accessible."""
    return Path(_R_EXECUTABLE).exists()


def r_check() -> dict:
    """Validate full R stack: executable + jsonlite package.

    Returns {"ok": True} or {"ok": False, "error": "..."}
    """
    if not r_available():
        return {"ok": False, "error": f"Rscript not found at {_R_EXECUTABLE}"}
    result = subprocess.run(
        [_R_EXECUTABLE, "--vanilla", "-e",
         '.libPaths(c(Sys.getenv("USERPROFILE"), .libPaths())); library(jsonlite); cat("OK\\n")'],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or "OK" not in result.stdout:
        stderr = result.stderr.strip()
        return {"ok": False, "error": f"jsonlite not available: {stderr[:300]}"}
    return {"ok": True, "r_path": _R_EXECUTABLE}


def run_r_stat(analysis: str, df: pd.DataFrame, timeout: int = 60) -> dict:
    """
    Run an R statistical analysis on a pandas DataFrame.

    Args:
        analysis: One of 'cronbach_alpha', 'driver_regression', 'anova_analysis', 'factor_analysis'
        df: Input DataFrame (columns must match each script's expected format)
        timeout: Max seconds to wait for R process

    Returns:
        dict with analysis results, or {"error": "..."} on failure
    """
    if not r_available():
        return {"error": f"Rscript not found at {_R_EXECUTABLE}"}

    script_name = _SCRIPT_MAP.get(analysis)
    if not script_name:
        return {"error": f"Unknown analysis: {analysis}. Choose from {list(_SCRIPT_MAP)}"}

    script_path = _SCRIPTS_DIR / script_name
    if not script_path.exists():
        return {"error": f"Script not found: {script_path}"}

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path  = os.path.join(tmpdir, "input.csv")
        json_path = os.path.join(tmpdir, "output.json")

        df.to_csv(csv_path, index=True)

        env = os.environ.copy()
        # Ensure user R library is on path (jsonlite installed there)
        env.setdefault("R_LIBS_USER", os.path.expanduser("~"))

        try:
            result = subprocess.run(
                [_R_EXECUTABLE, "--vanilla", str(script_path), csv_path, json_path],
                capture_output=True, text=True, timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"R script timed out after {timeout}s"}
        except Exception as e:
            return {"error": f"subprocess error: {e}"}

        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            return {"error": f"R exited with code {result.returncode}",
                    "stderr": stderr[:500], "stdout": stdout[:200]}

        if not os.path.exists(json_path):
            return {"error": "R script did not produce output JSON",
                    "stderr": result.stderr[:300], "stdout": result.stdout[:200]}

        with open(json_path) as f:
            return json.load(f)


def install_r_packages(packages: list[str]) -> dict:
    """Install R packages via Rscript (run once during setup)."""
    if not r_available():
        return {"error": "Rscript not found"}
    pkg_list = ", ".join(f'"{p}"' for p in packages)
    cmd = f'install.packages(c({pkg_list}), repos="https://cran.r-project.org")'
    result = subprocess.run(
        [_R_EXECUTABLE, "--vanilla", "-e", cmd],
        capture_output=True, text=True, timeout=300,
    )
    return {"returncode": result.returncode, "stdout": result.stdout[-500:], "stderr": result.stderr[-500:]}
