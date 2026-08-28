"""Live acceptance test for the schema-driven ingestion rewrite.

Runs the full pipeline (build_context_packet -> classify_all_questions ->
assignment_from_schema -> load_confirmed_assignment) against the REAL
Akshayakalpa datamap/data/AP files and checks the resulting awareness-funnel
bucket counts against this session's verified baseline. Marked `live` because
it calls a real (free-tier) OpenRouter LLM — deselected by default; run with
`-m live`.

If bucket counts don't match, that's real signal about what the LLM
misclassified. Do not fudge the assertions to make this pass.
"""
import os
import sqlite3

import pandas as pd
import pytest
from dotenv import load_dotenv

from infoleap.ingestion.schema_ingest import (
    build_context_packet,
    classify_all_questions,
    flatten_datamap_to_text,
)
from infoleap.ingestion.generic_loader import (
    assignment_from_schema,
    create_minimal_schema,
    load_confirmed_assignment,
)

DATAMAP = r"C:\Users\tuhin\Downloads\Akshaykalpa project\ENERGIZED 5_DATAMAP.xls"
DATAFILE = (
    r"D:\antigravity project\infoleap project\info-leap\oxdata\data\raw"
    r"\akshayakalpa\Updated Datafile_900_23 Feb.xlsx"
)
AP_FILE = (
    r"D:\antigravity project\infoleap project\info-leap\oxdata\data\raw"
    r"\akshayakalpa\Energized-5 New_AP_v3.xlsx"
)

BRAND_NAMES = [
    "Amul", "Nandini", "Aavin", "Milma", "Heritage", "Dodla", "Hatsun", "Arokya",
    "Akshayakalpa", "Akshayakalpa Organic", "Sudha", "Gowardhan", "Nestle",
    "Mother Dairy", "Vijaya", "Parag", "Country Delight", "Milky Mist",
    "Sid's Farm", "Masqati", "Uzhavarbumi", "Provilac", "Pride Of Cows",
]

EXPECTED_BUCKET_COUNTS = {
    "TOM": 895, "SPONT": 1939, "AIDED": 3814, "CONSIDERATION": 4178,
    "EVER_USED": 3004, "CURRENT_USER": 6066, "PREFERRED": 895,
}
EXPECTED_DIM_BRAND_COUNT = 23


def _flatten_ap_to_text(path: str) -> str:
    """The AP file's sheets ('AP', 'TOP BREAK', 'Netting') don't match the
    Variables/Values datamap shape, so dump every sheet as plain text instead
    of trying to force flatten_datamap_to_text onto it. This test only needs
    SOME real AP content in the context packet, not a perfectly parsed one.
    """
    xls = pd.ExcelFile(path)
    lines = []
    try:
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            lines.append(f"=== SHEET: {sheet_name} ===")
            lines.append(" | ".join(str(c) for c in df.columns))
            for _, row in df.iterrows():
                cells = [str(v) for v in row.tolist() if pd.notna(v)]
                if cells:
                    lines.append(" | ".join(cells))
    finally:
        xls.close()
    return "\n".join(lines)


@pytest.mark.live
def test_akshayakalpa_full_pipeline_matches_baseline(tmp_path):
    load_dotenv("oxdata/.env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set — skipping live regression test")
    model = os.environ.get("OPENROUTER_MODEL_PRO", "meta-llama/llama-3.3-70b-instruct")

    df = pd.read_excel(DATAFILE)
    # NOTE: build_context_packet's row sample gets json.dumps'd inside
    # classify_all_questions (schema_ingest.py), which raises TypeError on
    # pandas Timestamp cells — the real raw datafile has at least one datetime
    # column. This is a genuine gap in production code (build_context_packet /
    # classify_all_questions never sanitize non-JSON-native dtypes), but per
    # this task's scope schema_ingest.py's existing functions are not to be
    # touched, so the workaround lives here in the test instead: stringify any
    # datetime-like columns before handing the df to the pipeline.
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype(str)
    datamap_text = flatten_datamap_to_text(DATAMAP, "Variables", "Values")
    ap_text = _flatten_ap_to_text(AP_FILE)

    packet = build_context_packet(ap_text, datamap_text, df, sample_rows=15)
    schema_doc = classify_all_questions(
        packet, project_id="akshayakalpa_test", api_key=api_key, model=model,
    )

    pieces = assignment_from_schema(schema_doc)

    out_db_path = str(tmp_path / "infoleap.db")
    conn = sqlite3.connect(out_db_path)
    create_minimal_schema(conn)
    conn.close()

    load_confirmed_assignment(
        pieces["assignment"],
        pieces["value_labels_by_code"],
        pieces["shape_by_code"],
        df,
        out_db_path,
        brand_names=BRAND_NAMES,
        delimiter_by_code=pieces["delimiter_by_code"],
    )

    conn = sqlite3.connect(out_db_path)
    try:
        mismatches = []
        for stage, expected_count in EXPECTED_BUCKET_COUNTS.items():
            actual_count = conn.execute(
                "SELECT COUNT(*) FROM fact_brand_awareness WHERE stage = ?", (stage,)
            ).fetchone()[0]
            if actual_count != expected_count:
                mismatches.append(
                    f"{stage}: expected {expected_count}, got {actual_count}"
                )

        actual_dim_brand_count = conn.execute("SELECT COUNT(*) FROM dim_brand").fetchone()[0]
        if actual_dim_brand_count != EXPECTED_DIM_BRAND_COUNT:
            mismatches.append(
                f"dim_brand: expected {EXPECTED_DIM_BRAND_COUNT}, got {actual_dim_brand_count}"
            )
    finally:
        conn.close()

    assert not mismatches, "Bucket count mismatches vs baseline:\n" + "\n".join(mismatches)
