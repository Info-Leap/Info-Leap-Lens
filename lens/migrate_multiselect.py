"""
Migration: normalize multi-select value_text and populate value_rank1.

Affected variable_codes:
  bq1b  - Spontaneous brand recall  (ordered, first = top of mind)
  bq1c  - Aided brand awareness     (multi-select from screen)
  mq3a  - Kitchen appliances owned  (multi-select)
  mq3b  - Ranked recent purchases   (ranked, first = rank 1)

Problems fixed:
  1. Comma + leading-apostrophe format  e.g.  "'3,18,19"  -> "3 18 19"
  2. Leading apostrophe on single value e.g.  "'1"         -> "1"
  3. mq3b concatenated 2-digit pairs    e.g.  "40810"      -> "4 8 10"
     (survey tool dropped leading zero from what should be "040810")

Schema changes applied before this script:
  ALTER TABLE responses ADD COLUMN value_rank1 INTEGER;
"""

import sqlite3
import re


# ─── helpers ───────────────────────────────────────────────────────────────

def parse_standard(raw: str) -> list[int]:
    """
    Handle space-separated or comma-separated (with optional leading apostrophe).
    Returns list of ints, preserving original order.
    """
    # Strip leading apostrophe that spreadsheet tools inject
    s = raw.lstrip("'").strip()
    # Split on comma or whitespace
    tokens = re.split(r'[,\s]+', s)
    result = []
    for t in tokens:
        t = t.strip()
        if t.isdigit():
            result.append(int(t))
    return result


def decode_mq3b_concat(raw: str) -> list[int]:
    """
    Decode concatenated 2-digit-pair encoding used by the survey tool for mq3b.

    Appliance codes are 1-14, so each slot is always 2 digits (01-14).
    The survey tool stored the ranked sequence as a single integer, dropping
    any leading zero.

    e.g.  "40810"  was  "040810"  -> [04, 08, 10] -> [4, 8, 10]
          "309"    was  "0309"    -> [03, 09]      -> [3, 9]
          "114"    was  "0114"    -> [01, 14]      -> [1, 14]
    """
    s = raw.strip()
    # Pad to even length by prepending "0" if needed
    if len(s) % 2 == 1:
        s = "0" + s
    # Split into 2-char chunks and convert
    result = []
    for i in range(0, len(s), 2):
        chunk = s[i:i+2]
        val = int(chunk)
        if 1 <= val <= 14:          # valid appliance code
            result.append(val)
        # silently discard invalid codes (shouldn't happen)
    return result


def parse_mq3b(raw: str) -> list[int]:
    """
    mq3b can arrive as:
      - space-separated  "1 7 8"           -> already fine
      - concat 2-digit   "40810"           -> needs decode
      - comma+apostrophe "'1,7,8"          -> standard parse
      - single value     "1" or "14"       -> standard parse
    """
    s = raw.strip().lstrip("'")

    # If it contains space or comma, use standard parser
    if ' ' in s or ',' in s:
        return parse_standard(raw)

    # Single digit or double digit = single selection
    if s.isdigit() and len(s) <= 2:
        val = int(s)
        if 1 <= val <= 14:
            return [val]
        return []

    # Longer all-digit string = concatenated 2-digit pairs
    if s.isdigit() and len(s) > 2:
        return decode_mq3b_concat(s)

    return []


# ─── main migration ────────────────────────────────────────────────────────

MULTI_STANDARD = ('bq1b', 'bq1c', 'mq3a')
RANKED_MQ3B    = ('mq3b',)


def run():
    conn = sqlite3.connect('lens.db')
    cursor = conn.cursor()

    total_updated = 0
    total_skipped = 0
    parse_errors  = 0

    # ── 1. bq1b, bq1c, mq3a  (standard format cleanup) ──────────────────
    for vcode in MULTI_STANDARD:
        cursor.execute(
            "SELECT response_id, value_text, value_numeric "
            "FROM responses WHERE variable_code = ?",
            (vcode,)
        )
        rows = cursor.fetchall()

        for response_id, vt, vn in rows:
            items = []

            if vt is not None and isinstance(vt, str) and vt.strip():
                items = parse_standard(vt)
            elif vn is not None:
                # Already numeric single value (moved here by update_db.py)
                try:
                    items = [int(vn)]
                except (ValueError, TypeError):
                    pass

            if not items:
                total_skipped += 1
                continue

            normalized = ' '.join(str(x) for x in items)
            rank1      = items[0]

            cursor.execute(
                "UPDATE responses SET value_text = ?, value_rank1 = ? "
                "WHERE response_id = ?",
                (normalized, rank1, response_id)
            )
            total_updated += 1

    # ── 2. mq3b  (ranked, concat 2-digit pairs) ───────────────────────────
    cursor.execute(
        "SELECT response_id, value_text, value_numeric "
        "FROM responses WHERE variable_code = 'mq3b'"
    )
    rows = cursor.fetchall()

    for response_id, vt, vn in rows:
        items = []

        if vt is not None and isinstance(vt, str) and vt.strip():
            items = parse_mq3b(vt)
        elif vn is not None:
            try:
                items = parse_mq3b(str(int(vn)))
            except (ValueError, TypeError):
                pass

        if not items:
            total_skipped += 1
            continue

        normalized = ' '.join(str(x) for x in items)
        rank1      = items[0]

        cursor.execute(
            "UPDATE responses SET value_text = ?, value_rank1 = ? "
            "WHERE response_id = ?",
            (normalized, rank1, response_id)
        )
        total_updated += 1

    conn.commit()
    conn.close()

    print(f"Migration complete.")
    print(f"  Updated : {total_updated:,} rows")
    print(f"  Skipped : {total_skipped:,} rows (no parseable value)")


if __name__ == '__main__':
    run()
