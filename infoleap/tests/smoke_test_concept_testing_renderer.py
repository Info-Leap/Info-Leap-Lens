"""
Headless smoke test for concept_testing_renderer.py — calls the real render function against
real project matrices, outside a Streamlit script context, and reports any Python exception.

This exists because every regression this session (the 'str' object has no attribute 'get' crash,
twice) was only caught by manually clicking through the UI with Playwright after the fact. This
script catches the same class of bug in seconds, with no browser, no LLM calls, and no Streamlit
server — run it after any schema/matrix/renderer change, before touching a browser.

Usage:
    py -3.12 tests/smoke_test_concept_testing_renderer.py --project karat-coindcx
    py -3.12 tests/smoke_test_concept_testing_renderer.py --project karat-coindcx --tabs "Route Comparison"
"""
import argparse
import sys
import traceback
from pathlib import Path

_OXDATA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_OXDATA_DIR))
sys.path.insert(0, str(_OXDATA_DIR / "skills"))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    # Streamlit functions no-op safely outside a real script run (print a
    # "missing ScriptRunContext" warning to stderr, don't raise) — this is what lets us call
    # production render code headlessly and still catch genuine Python exceptions like the
    # AttributeError bugs found this session.
    import warnings
    import logging
    warnings.filterwarnings("ignore")
    logging.getLogger("streamlit").setLevel(logging.ERROR)

    from views.concept_testing_renderer import render_concept_testing

    proj = {"id": args.project, "name": args.project}

    def _dummy_call_openrouter(*a, **kw):
        return ""

    print(f"Smoke-testing render_concept_testing() for project '{args.project}'...")
    try:
        render_concept_testing(proj, _OXDATA_DIR, _dummy_call_openrouter)
    except Exception:
        print("\nFAIL — render_concept_testing() raised an exception:\n")
        traceback.print_exc()
        sys.exit(1)

    print("PASS — no exception raised across all tabs/sections.")
    print("Note: this catches crashes, not visual/logical correctness — still spot-check the UI "
          "for anything this can't see (styling, chart data plausibility, missing-but-not-crashing content).")


if __name__ == "__main__":
    main()
