"""
SchemaRegistry — Runtime SQLite schema introspection.

Replaces the hardcoded DATA_DICTIONARY string in researcher_agent.py with a
live, accurate schema derived from the actual database. Cached after first
call — no repeated DB hits per query.

Usage:
    registry = SchemaRegistry(db_path)
    context_str = registry.get_context()  # drop into LLM prompt
    schema_dict = registry.get_schema()   # structured dict for programmatic use
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class SchemaRegistry:
    """
    Introspects a SQLite database and returns a compact, LLM-readable schema.

    Views are prioritised (labelled 'VIEW — use these') because they join
    dimensions automatically. Raw fact/dim tables are listed but labelled
    'underlying table — prefer views'.
    """

    # Views the system actually uses — always listed first and prominently
    PREFERRED_VIEWS = {
        "v_respondents",
        "v_brand_awareness",
        "v_brand_nps",
        "v_kitchen_ownership",
        "v_recent_purchase",
        "v_room_appliances",
        # New views (ingested 2026-06-09)
        "v_brand_imagery",
        "v_satisfaction",
        "v_need_importance",
        "v_purchase_journey",
        "v_portfolio_awareness",
        "v_key_drivers",
    }

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._schema: Optional[dict] = None   # {table_name: {"type": "view"|"table", "columns": [...]}}
        self._context: Optional[str] = None   # cached LLM string

    # ── Public API ──────────────────────────────────────────────────────────

    def get_schema(self) -> dict:
        """Return structured schema dict. Cached after first call."""
        if self._schema is None:
            self._schema = self._introspect()
        return self._schema

    def get_context(self) -> str:
        """Return LLM-readable schema string. Cached after first call."""
        if self._context is None:
            self._context = self._format(self.get_schema())
        return self._context

    def invalidate(self) -> None:
        """Force re-introspection on next call (e.g., after schema migration)."""
        self._schema = None
        self._context = None

    def get_view_columns(self, view_name: str) -> list[str]:
        """Return column names for a specific view/table."""
        schema = self.get_schema()
        entry = schema.get(view_name, {})
        return [col["name"] for col in entry.get("columns", [])]

    # ── Internal ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self._db_path}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _introspect(self) -> dict:
        schema: dict = {}
        conn = self._connect()
        try:
            cur = conn.cursor()

            # Get all tables and views
            cur.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type DESC, name"   # views first
            )
            objects = cur.fetchall()

            for name, obj_type in objects:
                try:
                    cur.execute(f"PRAGMA table_info({name})")
                    cols = cur.fetchall()
                    # (cid, name, type, notnull, dflt_value, pk)
                    columns = [
                        {"name": col[1], "type": col[2] or "TEXT", "pk": bool(col[5])}
                        for col in cols
                    ]
                    schema[name] = {"type": obj_type, "columns": columns}
                except Exception:
                    pass  # skip unreadable objects

        finally:
            conn.close()

        return schema

    def _format(self, schema: dict) -> str:
        """Build compact, LLM-readable schema string."""
        lines = ["=== DATABASE SCHEMA (live introspection) ===\n"]

        # Preferred views first
        lines.append("## VIEWS — USE THESE IN QUERIES (dimensions auto-joined)\n")
        for name in sorted(self.PREFERRED_VIEWS):
            entry = schema.get(name)
            if entry:
                cols = ", ".join(
                    f"{c['name']} ({c['type']})" for c in entry["columns"]
                )
                lines.append(f"  {name}: {cols}")

        # Other views
        other_views = [
            n for n, e in schema.items()
            if e["type"] == "view" and n not in self.PREFERRED_VIEWS
        ]
        if other_views:
            lines.append("\n## OTHER VIEWS\n")
            for name in sorted(other_views):
                entry = schema[name]
                cols = ", ".join(c["name"] for c in entry["columns"])
                lines.append(f"  {name}: {cols}")

        # Tables
        tables = [n for n, e in schema.items() if e["type"] == "table"]
        if tables:
            lines.append("\n## UNDERLYING TABLES (prefer views above)\n")
            for name in sorted(tables):
                entry = schema[name]
                cols = ", ".join(c["name"] for c in entry["columns"])
                lines.append(f"  {name}: {cols}")

        lines.append("\n=== SQL RULES ===")
        lines.append("- ALWAYS use views (v_*) not raw fact/dim tables")
        lines.append("- SQLite dialect: LIMIT not TOP, no FETCH FIRST, LIKE not ILIKE")
        lines.append("- Use COUNT(DISTINCT respondent_id) for unique person counts")
        lines.append("- Join v_brand_awareness or v_brand_nps to v_respondents for demographic filters")
        lines.append("- category column in v_respondents is NULL for all rows — do not filter on it")
        lines.append("- v_brand_awareness.stage values: TOM, SPONT, AIDED, EVER_USED, CURRENT_USER, CONSIDERATION, PREFERRED, LAST_PURCHASED")
        lines.append("- Full brand funnel order: AIDED > SPONT > TOM > EVER_USED > CONSIDERATION > CURRENT_USER > PREFERRED > LAST_PURCHASED")
        lines.append("- v_satisfaction: score 0-10 CSAT (bq5), only recent buyers have this (n=4704)")
        lines.append("- v_need_importance: importance score 1-7 per attr_id, section_code 1-5 (broad feature sections)")
        lines.append("- v_key_drivers: prejoins importance avg + brand association % — use for importance-performance analysis; filter by brand_id first (3-4s query)")
        lines.append("- v_purchase_journey: pq_var in ('pq1','pq2','pq3','pq3_oth','pq4','pq5'); pq1 has category_code; pq2-5 are cross-category")

        return "\n".join(lines)


# ── Module-level singleton factory ────────────────────────────────────────────

_registry_cache: dict[str, SchemaRegistry] = {}


def get_registry(db_path: str | Path) -> SchemaRegistry:
    """Return cached SchemaRegistry for this db_path."""
    key = str(db_path)
    if key not in _registry_cache:
        _registry_cache[key] = SchemaRegistry(db_path)
    return _registry_cache[key]
