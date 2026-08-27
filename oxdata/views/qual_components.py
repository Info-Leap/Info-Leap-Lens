"""
Streamlit components for rendering qualitative research findings.
"""

import os
import sys

# Ensure we can find packages from project root (parent of oxdata)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir) # This is 'oxdata'
repo_root = os.path.dirname(project_root)

if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import streamlit as st
from oxdata.views.chart_renderer import render_result

def render_qual_result(qual_data: dict, is_columnar: bool = False):
    """Render themes and quotes from qualitative synthesis with modern card-based UI."""
    if not qual_data or (not qual_data.get("themes") and not qual_data.get("passages")):
        st.info("No specific qualitative themes found for this query.")
        return

    # Use a container with a subtle background for the entire qual section
    with st.container():
        st.markdown("### 🎙️ Consumer Insights")

        if qual_data.get("core_category"):
            st.markdown(f"**Research Focus:** `{qual_data['core_category']}`")

        # 1. Thematic Grid
        st.markdown("#### Key Discovery Themes")
        themes = qual_data.get("themes", [])
        if themes:
            # Render themes as modern "Cards" using columns
            cols = st.columns(min(len(themes), 3))
            for i, theme in enumerate(themes):
                with cols[i % len(cols)]:
                    st.markdown(f"""
                    <div style="background-color: rgba(30, 144, 255, 0.05); border: 1px solid rgba(30, 144, 255, 0.2); padding: 15px; border-radius: 10px; height: 100%;">
                        <h5 style="margin-top: 0; color: #1E90FF;">{theme['name']}</h5>
                        <p style="font-size: 0.85rem; line-height: 1.4;">{theme.get('definition', '')}</p>
                    </div>
                    """, unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

        # 2. All Supporting Quotes
        st.markdown("#### Verbatim Consumer Voices (All Matches)")

        # Pull quotes from both structured themes and raw passages if available
        all_quotes = []
        seen_content = set()

        # Themes quotes
        for t in themes:
            if t.get("quote") and t["quote"] not in seen_content:
                all_quotes.append({"topic": t["name"], "quote": t["quote"]})
                seen_content.add(t["quote"])

        # Raw passages (the "All possible matches" part)
        for p in qual_data.get("all_passages", []):
            if p.get("content") and p["content"] not in seen_content:
                all_quotes.append({"topic": p.get("section_title", "Related Context"), "quote": p["content"]})
                seen_content.add(p["content"])

        if all_quotes:
            # Use an expander for "All Matches" to keep UI clean but accessible
            with st.container():
                for item in all_quotes:
                    st.markdown(f"""
                    <div style="border-left: 4px solid #cbd5e1; padding-left: 15px; margin-bottom: 20px;">
                        <p style="font-style: italic; font-size: 0.95rem; margin-bottom: 5px;">"{item['quote']}"</p>
                        <p style="font-size: 0.75rem; color: #64748b; margin: 0; text-transform: uppercase; letter-spacing: 0.5px;">Topic: <b>{item['topic']}</b></p>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.caption("No specific verbatim quotes found.")

def render_mixed_insight(answer: str, qual_data: dict, df=None, sql=""):
    """Renders a clean, summary-first integrated view for mixed-mode queries."""
    st.markdown("### 🧠 Integrated Research Analysis")

    # 1. Main Result (The 'Hero' Answer)
    # We use st.info or standard markdown to ensure theme-aware readability
    st.markdown(f"{answer}")
    st.divider()

    # 2. Detailed Evidence in professional expanders
    with st.expander("📊 View Statistical Evidence (Quantitative)", expanded=False):
        if df is not None and not df.empty:
            from oxdata.views.chart_renderer import render_result
            render_result(df, "Quant Results")
            if sql:
                st.caption("SQL Trace")
                st.code(sql, language="sql")
        else:
            st.info("No quantitative data matched this specific segment.")

    with st.expander("🎙️ View Consumer Insights (Qualitative)", expanded=False):
        render_qual_result(qual_data, is_columnar=False)

