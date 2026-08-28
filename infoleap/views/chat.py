import os
import sys
import asyncio
import time
import re
import pandas as pd
import streamlit as st
import plotly.express as px

# Ensure we can find packages from project root
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from infoleap.researcher_agent import run_autonomous_research_async
from infoleap.skills.deterministic_pipeline import run_deterministic_async, is_qualitative
import infoleap.sheets_logger as sheets_logger
from infoleap.views.chart_renderer import render_result
from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                    section_header, empty_state)

# Apply Pulse Styles
inject_pulse_styles()
sidebar_context_block()

def render_glide_loader(thoughts):
    """Renders the exact GLIDE steps from the video."""
    exact_steps = [
        "READING 10,000 respondents across two assets...",
        "FILTERING to Tier 2 cities Kolkata, Lucknow, Gorakhpur...",
        "RESOLVING Q&Q (NPS) across both datamap versions...",
        "CROSS-CHECKING 18 qual transcripts for convergent evidence...",
        "WRITING answer with citations..."
    ]
    # Use thoughts passed from the agent or simulated if none
    display_steps = thoughts if thoughts else exact_steps
    
    with st.container():
        st.markdown('<div class="pulse-card" style="padding: 15px; border-left: 4px solid #30a76a;">', unsafe_allow_html=True)
        st.caption("GLIDE: Processing Intelligence")
        for step in display_steps:
            st.markdown(f'<div class="glide-step"><span class="glide-step-check">âœ“</span> {step}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

def render_pulse_response(data, df, sql=None, latency=None, verbatims=None, brand_health_fig=None, engine=None, msg_idx=0):
    """Pulse-themed assistant response with interactive Evidence Drawer."""
    if data is None:
        st.warning("Response unavailable.")
        return

    # 1. Main Chat Layout
    if st.session_state.get("active_evidence"):
        main_col, evidence_col = st.columns([1.5, 1])
    else:
        main_col = st.container()
        evidence_col = None

    summary_text = getattr(data, "summary", "") or ""
    with main_col:
        section_header("Pulse answered")
        # Render summary as markdown so bold/bullets/headers work correctly.
        # Use st.success() for the green card styling (renders markdown natively).
        st.success(summary_text)

        # Data Visualization
        section_header("Evidence Visualization")
        if brand_health_fig is not None:
            # Pre-built brand health chart â€” render directly
            import plotly.graph_objects as go
            brand_health_fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(brand_health_fig, use_container_width=True)
        elif df is not None and not df.empty:
            render_result(
                df, "",
                chart_spec=getattr(data, "chart_spec", None),
                chart_code=getattr(data, "chart_code", None),
            )

        # Sources Chips (Dynamic from Agent verbatims)
        if verbatims:
            st.markdown('**SOURCES**')
            v_cols = st.columns(min(len(verbatims[:3]), 3))
            for i, v in enumerate(verbatims[:3]):
                label = f"ðŸ—£ï¸ {v['source']}: {v.get('brand','N/A')}"
                if v_cols[i].button(label, use_container_width=True, key=f"v_chip_{msg_idx}_{i}"):
                    st.session_state.active_evidence = v
                    st.rerun()

    # 2. Evidence Drawer (Dynamic Binding)
    active_v = st.session_state.get("active_evidence")
    if evidence_col and active_v:
        with evidence_col:
            st.markdown('<div class="pulse-card" style="border-left: 4px solid #30a76a;">', unsafe_allow_html=True)
            st.markdown(f"### EVIDENCE: {active_v['source']}")
            city_label  = active_v.get('city', 'National')
            brand_label = active_v.get('brand', '')
            parts = [p for p in [brand_label, city_label, "Wave 1"] if p]
            st.caption(" Â· ".join(parts))
            st.divider()
            st.markdown(f"""
                <div style="font-size: 1rem; line-height: 1.6; font-style: italic; color: #166534;">
                "{active_v['text']}"
                </div>
            """, unsafe_allow_html=True)
            if st.button("Close Evidence", use_container_width=True):
                st.session_state.active_evidence = None
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
    
    # 4. Technical Audit (Collapsed)
    with st.expander("ðŸ› ï¸ System Trace", expanded=False):
        if sql: st.code(sql, language="sql")
        engine_label = engine or "LENS 3.2"
        trace = getattr(data, "diagnostic_trace", "") or ""
        st.caption(f"Latency: {latency}ms | Engine: {engine_label} | PII Scrubbed")
        if trace:
            st.caption(trace)

# SYNC-TO-ASYNC GEN WRAPPER
def get_events_sync(prompt, history, session_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    active_cat   = st.session_state.get("active_category", "All")
    active_brand = st.session_state.get("active_brand", "All Brands")

    # â”€â”€ ROUTING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Always use the full agent â€” it handles both quant and qual, maintains
    # session context, compares entities, and produces rich narrative responses.
    # The deterministic path is kept only as a fallback (never primary).
    use_deterministic = False

    def _run_gen(gen):
        while True:
            try:
                yield loop.run_until_complete(gen.__anext__())
            except StopAsyncIteration:
                break
            except Exception as e:
                yield {"type": "error", "content": str(e)}
                break

    try:
        if use_deterministic:
            # Run deterministic path; if it yields an error, fall back to agent
            events = []
            had_result = False
            had_error = False
            error_msg = ""
            for ev in _run_gen(run_deterministic_async(prompt, message_history=history)):
                events.append(ev)
                if ev.get("type") == "result":
                    had_result = True
                elif ev.get("type") == "error":
                    had_error = True
                    error_msg = ev.get("content", "")

            if had_result:
                # Deterministic path succeeded â€” emit all events
                yield from events
            else:
                # Deterministic failed â†’ fall back to pydantic_ai agent silently
                yield {"type": "thought", "content": f"Switching to full agent (deterministic path: {error_msg[:80]})"}
                gen = run_autonomous_research_async(
                    prompt,
                    message_history=history,
                    session_id=session_id,
                    active_cat=active_cat,
                    active_brand=active_brand,
                )
                yield from _run_gen(gen)
        else:
            gen = run_autonomous_research_async(
                prompt,
                message_history=history,
                session_id=session_id,
                active_cat=active_cat,
                active_brand=active_brand,
            )
            yield from _run_gen(gen)
    finally:
        loop.close()

# --- CHAT UI ---
section_header("Ask Pulse", "Intelligence across 6,847 respondents & 233 interviews")

if "messages" not in st.session_state: st.session_state.messages = []
if "or_session_id" not in st.session_state:
    import uuid
    st.session_state.or_session_id = f"pulse_{uuid.uuid4().hex[:8]}"

# Render History
_asst_idx = 0
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            render_pulse_response(msg["data"], msg["df"], msg.get("sql"), msg.get("latency"), msg.get("verbatims"), brand_health_fig=msg.get("brand_health_fig"), engine=msg.get("engine"), msg_idx=_asst_idx)
            _asst_idx += 1

# Input
if prompt := st.chat_input("Ask about market trends, brand health, or consumer feedback..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    with st.chat_message("assistant"):
        thought_placeholder = st.empty()
        all_thoughts = []
        
        start_time = time.time()
        data_final, df_final, sql_final, verbatims_final = None, None, None, []
        brand_health_fig_final = None
        engine_final = None

        for event in get_events_sync(prompt, st.session_state.messages, st.session_state.or_session_id):
            if not isinstance(event, dict):
                continue  # guard: skip None or non-dict events
            etype = event.get("type", "")
            if etype == "thought":
                all_thoughts.append(event.get("content", "").strip())
                with thought_placeholder.container():
                    render_glide_loader(all_thoughts)
            elif etype == "result":
                data_final            = event.get("data")
                df_final              = event.get("df")
                sql_final             = event.get("sql")
                verbatims_final       = event.get("verbatims", [])
                brand_health_fig_final = event.get("brand_health_fig")
                engine_final          = event.get("engine")
                thought_placeholder.empty()
            elif etype == "error":
                thought_placeholder.empty()
                err_msg = event.get("content", "Unknown error")
                st.error(f"**Pulse encountered an issue:** {err_msg}")
                st.caption("Try rephrasing or breaking the question into smaller parts.")
                break

        if data_final:
            latency = int((time.time() - start_time) * 1000)
            render_pulse_response(
                data_final, df_final, sql_final, latency, verbatims_final,
                brand_health_fig=brand_health_fig_final,
                engine=engine_final,
                msg_idx=len([m for m in st.session_state.messages if m["role"] == "assistant"]),
            )

            st.session_state.messages.append({
                "role": "assistant",
                "data": data_final, "df": df_final, "sql": sql_final,
                "latency": latency, "verbatims": verbatims_final,
                "brand_health_fig": brand_health_fig_final,
                "engine": engine_final,
            })
            
            # Log interaction
            sheets_logger.log_interaction(
                session_id=st.session_state.or_session_id,
                question=prompt, skill="pulse_chat", sql=sql_final or "",
                df=df_final, answer=data_final.summary, reasoning=data_final.thinking,
                latency_ms=latency, source="PULSE_STREAMLIT"
            )
