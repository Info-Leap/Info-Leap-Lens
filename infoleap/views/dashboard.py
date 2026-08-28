import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from infoleap.utils.ui_styles import (inject_pulse_styles, sidebar_context_block,
                                     section_header as _section_title_fn,
                                     kpi_card as _kpi_pill_fn,
                                     empty_state as _empty_state_fn,
                                     BRAND_COLORS, CHART_LAYOUT)
from infoleap.utils.dashboard_engine import (
    get_cached_dashboard_data,
    get_home_headline_stats,
    get_awareness_funnel_data,
    get_nps_composition,
    get_salience_scatter_data,
    get_city_dominance,
    get_category_brand_matrix,
    get_verbatim_pulse,
    get_hidden_hooks,
    get_appliance_ownership_story,
    get_recent_purchase_story,
)

inject_pulse_styles()

BRAND_PALETTE = BRAND_COLORS  # now sourced from shared tokens


def _section_title(title: str, subtitle: str = ""):
    _section_title_fn(title, subtitle)


def _kpi_pill(label: str, value: str, color: str = "#6366f1"):
    _kpi_pill_fn(label, value, color)


def _cl(**overrides) -> dict:
    """Merge shared CHART_LAYOUT with chart-specific overrides."""
    base = dict(CHART_LAYOUT)
    base.update(overrides)
    return base


def show_dashboard():
    project_id = st.session_state.get("active_project_id", "project_1")
    dash_data = get_cached_dashboard_data("All", project_id=project_id)
    stats     = dash_data["stats"]

    sidebar_context_block(brand="All Brands", respondents=stats["respondents"])

    head = get_home_headline_stats(project_id=project_id)

    # Load project meta for dynamic header
    import json as _json
    from pathlib import Path as _Path
    _meta_path = _Path(f"oxdata/data/{project_id}/project_meta.json")
    _meta = _json.loads(_meta_path.read_text()) if _meta_path.exists() else {}
    _proj_label = _meta.get("description") or _meta.get("display_name") or project_id

    # ── Hero header ──────────────────────────────────────────────────────────
    h_col, d_col = st.columns([0.8, 0.2])
    with h_col:
        st.markdown(
            "<div style='font-size:1.55rem;font-weight:900;color:#0f172a;letter-spacing:-0.01em;'>"
            "Market Intelligence Command Center</div>"
            f"<div style='font-size:0.82rem;color:#64748b;margin-top:2px;'>{_proj_label}</div>",
            unsafe_allow_html=True,
        )
    with d_col:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.8rem;margin-top:14px;'>"
            f"📅 {dash_data['timestamp']}</div>",
            unsafe_allow_html=True,
        )

    # ── Executive KPI scorecard (6 accent cards) ─────────────────────────────
    def _home_kpi(col, label, value, sub="", color="#1a5d4d"):
        with col:
            st.markdown(
                f"<div style='text-align:center;padding:15px 8px 11px 8px;border-radius:12px;"
                f"border:1px solid #e5e7eb;background:#ffffff;border-top:3px solid {color};"
                f"box-shadow:0 1px 3px rgba(0,0,0,0.04);height:100%;'>"
                f"<div style='font-size:0.55rem;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:0.08em;color:#9ca3af;margin-bottom:5px;'>{label}</div>"
                f"<div style='font-size:1.7rem;font-weight:900;color:{color};line-height:1.05;'>{value}</div>"
                f"<div style='font-size:0.62rem;color:#6b7280;margin-top:5px;line-height:1.25;"
                f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{sub}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    _ml = head.get("market_leader") or {}
    _lov = head.get("most_loved") or {}
    _gap = head.get("salience_gap") or {}
    kc = st.columns(6)
    _home_kpi(kc[0], "Respondents", f"{head['base_n']:,}", "across 18 cities", "#6366f1")
    _home_kpi(kc[1], "Brands Tracked", f"{head['n_brands']}", f"{head['n_nps_brands']} with NPS base", "#0ea5e9")
    _home_kpi(kc[2], "Market Leader",
              _ml.get("brand", "—"),
              f"TOM {_ml.get('tom',0):.1f}% · reach {_ml.get('aided',0):.0f}%", "#1a5d4d")
    _home_kpi(kc[3], "Most Loved",
              _lov.get("brand", "—"),
              f"NPS {_lov.get('nps',0):+.0f} · n={_lov.get('raters',0):,}", "#22c55e")
    _home_kpi(kc[4], "Avg Category NPS",
              f"{head.get('avg_nps',0):+.0f}" if head.get("avg_nps") is not None else "—",
              f"respondent-weighted · {head.get('n_neg_nps',0)} brand(s) negative", "#f59e0b")
    _home_kpi(kc[5], "TOM Concentration", f"{head.get('top5_share',0):.0f}%",
              "top-5 share of first recall", "#8b5cf6")

    # ── AI executive read-out ────────────────────────────────────────────────
    _findings = []
    if _ml:
        _findings.append(
            f"<b>{_ml['brand']}</b> owns the category — first-named by <b>{_ml['tom']:.1f}%</b> "
            f"and recognised by <b>{_ml['aided']:.0f}%</b> of {head['base_n']:,} respondents.")
    if _lov and _ml and _lov["brand"] != _ml["brand"]:
        _findings.append(
            f"Loyalty leadership sits elsewhere: <b>{_lov['brand']}</b> posts the strongest "
            f"NPS at <b>{_lov['nps']:+.0f}</b> — advocacy and salience are not the same brand.")
    if _gap:
        _findings.append(
            f"Biggest reach-without-recall gap: <b>{_gap['brand']}</b> is known by "
            f"{_gap['aided']:.0f}% but top-of-mind for only {_gap['tom']:.1f}% — a salience "
            f"opportunity, not an awareness problem.")
    if head.get("n_neg_nps"):
        _findings.append(
            f"<b>{head['n_neg_nps']}</b> of {head['n_nps_brands']} brands carry a "
            f"<b>negative NPS</b> — detractors outnumber promoters.")
    _bullets = "".join(
        f"<div style='font-size:0.83rem;margin-bottom:6px;line-height:1.5;color:#334155;'>• {f}</div>"
        for f in _findings[:4])
    st.markdown(
        f"<div style='background:linear-gradient(135deg,#f0fdf4,#eff6ff);border-radius:12px;"
        f"padding:14px 18px;border-left:4px solid #22c55e;margin-top:14px;'>"
        f"<div style='font-size:0.7rem;font-weight:800;text-transform:uppercase;letter-spacing:0.06em;"
        f"color:#16a34a;margin-bottom:8px;'>🧠 Executive Read-out</div>{_bullets}</div>",
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Section 2: Awareness Battle ──────────────────────────────────────────
    _section_title(
        "📣 The Awareness Battle",
        "Aided = who knows the brand | Spontaneous = unprompted recall | TOM = first-named",
    )
    funnel_df = get_awareness_funnel_data(project_id=project_id)
    if not funnel_df.empty:
        funnel_sorted = funnel_df.sort_values("aided_pct", ascending=True)
        brands_f = funnel_sorted["brand_name"].tolist()
        fig_funnel = go.Figure()
        # barmode="overlay" draws each trace on top of the previous one — since
        # aided_pct ≥ spont_pct ≥ tom_pct always, the widest bar must be added FIRST
        # so narrower bars stay visible on top of it, not the other way round.
        for metric, color, label in [
            ("aided_pct", "#d8b4fe", "Aided Awareness"),
            ("spont_pct", "#8b5cf6", "Spontaneous"),
            ("tom_pct",   "#6366f1", "Top-of-Mind"),
        ]:
            fig_funnel.add_trace(go.Bar(
                y=brands_f,
                x=funnel_sorted[metric].tolist(),
                name=label,
                orientation="h",
                marker_color=color,
                text=[f"{v:.1f}%" for v in funnel_sorted[metric]],
                textposition="inside",
            ))
        fig_funnel.update_layout(
            barmode="overlay",
            xaxis_title="% of Respondents",
            height=380,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
            xaxis=dict(gridcolor="#f0f0f0", range=[0, funnel_sorted["aided_pct"].max() * 1.15]),
        )
        st.plotly_chart(fig_funnel, use_container_width=True)
        st.caption(
            "Bars overlay — wider gap between Aided and TOM = high awareness but low salience. "
            "Brands with TOM ≈ Aided punch above their weight."
        )
    else:
        st.info("Awareness funnel data unavailable.")

    st.divider()

    # ── Section 3: NPS + Salience Efficiency ─────────────────────────────────
    _section_title(
        "❤️ Loyalty & Salience Efficiency",
        "Left: who has promoters vs detractors | Right: who converts reach into top-of-mind recall",
    )
    nps_col, scatter_col = st.columns([6, 4])

    with nps_col:
        nps_df = get_nps_composition(project_id=project_id)
        if not nps_df.empty:
            nps_sorted = nps_df.sort_values("nps", ascending=True)
            brands_n = nps_sorted["brand_name"].tolist()
            fig_nps = go.Figure()
            fig_nps.add_trace(go.Bar(
                y=brands_n,
                x=(-nps_sorted["detractor_pct"]).tolist(),
                name="Detractors",
                orientation="h",
                marker_color="#ef4444",
                text=[f"-{v:.0f}%" for v in nps_sorted["detractor_pct"]],
                textposition="inside",
            ))
            fig_nps.add_trace(go.Bar(
                y=brands_n,
                x=nps_sorted["passive_pct"].tolist(),
                name="Passives",
                orientation="h",
                marker_color="#d1d5db",
                text=[f"{v:.0f}%" for v in nps_sorted["passive_pct"]],
                textposition="inside",
            ))
            fig_nps.add_trace(go.Bar(
                y=brands_n,
                x=nps_sorted["promoter_pct"].tolist(),
                name="Promoters",
                orientation="h",
                marker_color="#22c55e",
                text=[f"{v:.0f}%" for v in nps_sorted["promoter_pct"]],
                textposition="inside",
            ))
            fig_nps.add_vline(x=0, line_color="#374151", line_width=1.5)
            fig_nps.update_layout(
                barmode="relative",
                title="NPS Composition (Detractors ← | → Promoters)",
                height=360,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
                xaxis=dict(gridcolor="#f0f0f0", title="% of Raters"),
            )
            st.plotly_chart(fig_nps, use_container_width=True)
            st.caption("Sorted by NPS score. Wide green = strong loyalty engine. Wide red = active detractor risk.")
        else:
            st.info("NPS data unavailable.")

    with scatter_col:
        sal_df = get_salience_scatter_data(project_id=project_id)
        if not sal_df.empty:
            max_val = max(sal_df["aided_pct"].max(), sal_df["tom_pct"].max()) * 1.1
            fig_sal = go.Figure()
            fig_sal.add_trace(go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines",
                line=dict(dash="dot", color="#9ca3af", width=1),
                showlegend=False,
                hoverinfo="skip",
            ))
            fig_sal.add_trace(go.Scatter(
                x=sal_df["aided_pct"].tolist(),
                y=sal_df["tom_pct"].tolist(),
                mode="markers+text",
                text=sal_df["brand_name"].tolist(),
                textposition="top center",
                textfont=dict(size=9),
                marker=dict(
                    size=10,
                    color=BRAND_PALETTE[:len(sal_df)],
                    line=dict(width=1, color="white"),
                ),
                hovertemplate="<b>%{text}</b><br>Aided: %{x:.1f}%<br>TOM: %{y:.1f}%<extra></extra>",
                showlegend=False,
            ))
            fig_sal.update_layout(
                title="Salience Efficiency",
                xaxis_title="Aided Awareness %",
                yaxis_title="Top-of-Mind %",
                height=360,
                margin=dict(l=10, r=10, t=40, b=10),
                xaxis=dict(gridcolor="#f0f0f0"),
                yaxis=dict(gridcolor="#f0f0f0"),
            )
            st.plotly_chart(fig_sal, use_container_width=True)
            st.caption("Above diagonal = converts awareness to recall efficiently.")
        else:
            st.info("Salience data unavailable.")

    st.divider()

    # ── Section 4: Geographic Story ──────────────────────────────────────────
    _section_title(
        "🗺️ Geographic Story — City Dominance",
        "Lead brand TOM % and dominance gap in each city — wide gap = concentrated market",
    )
    city_df = get_city_dominance(project_id=project_id)
    if not city_df.empty:
        city_sorted = city_df.sort_values("top_brand_pct", ascending=True)
        unique_brands = city_sorted["top_brand"].unique().tolist()
        brand_color_map = {b: BRAND_PALETTE[i % len(BRAND_PALETTE)] for i, b in enumerate(unique_brands)}
        bar_colors = [brand_color_map[b] for b in city_sorted["top_brand"]]

        fig_city = go.Figure()
        fig_city.add_trace(go.Bar(
            y=city_sorted["city_name"].tolist(),
            x=city_sorted["top_brand_pct"].tolist(),
            orientation="h",
            marker_color=bar_colors,
            text=[f"{row['top_brand']} {row['top_brand_pct']:.1f}%" for _, row in city_sorted.iterrows()],
            textposition="inside",
            customdata=city_sorted[["top_brand","gap"]].values,
            hovertemplate="<b>%{y}</b><br>Leader: %{customdata[0]}<br>TOM: %{x:.1f}%<br>Gap: +%{customdata[1]:.1f}pp<extra></extra>",
        ))
        fig_city.add_trace(go.Bar(
            y=city_sorted["city_name"].tolist(),
            x=city_sorted["second_brand_pct"].tolist(),
            orientation="h",
            marker_color="rgba(200,200,200,0.35)",
            showlegend=False,
            hoverinfo="skip",
        ))
        fig_city.update_layout(
            barmode="overlay",
            title="Lead Brand TOM % by City (colored bar = leader, light = runner-up)",
            height=420,
            margin=dict(l=10, r=10, t=40, b=10),
            xaxis=dict(gridcolor="#f0f0f0", title="% TOM Share"),
        )
        st.plotly_chart(fig_city, use_container_width=True)
        st.caption(
            "Bar color = lead brand. Light overlay = runner-up. "
            "Large gap = concentrated market. Bars nearly equal = fragmented (contestable)."
        )
    else:
        st.info("City dominance data unavailable.")

    st.divider()

    # ── Section 5: Category × Brand Matrix ───────────────────────────────────
    _section_title(
        "🏷️ Category × Brand — Who Owns What",
        "Awareness % within each product category. Category specialists vs. pan-category generalists.",
    )
    cat_matrix = get_category_brand_matrix(project_id=project_id)
    if not cat_matrix.empty:
        fig_cat = go.Figure(data=go.Heatmap(
            z=cat_matrix.values.tolist(),
            x=list(cat_matrix.columns),
            y=list(cat_matrix.index),
            colorscale="Blues",
            text=[[f"{v:.1f}%" for v in row] for row in cat_matrix.values],
            texttemplate="%{text}",
            showscale=True,
            colorbar=dict(title="Awareness %"),
        ))
        fig_cat.update_layout(
            title="Brand × Category Awareness Matrix",
            xaxis_title="Product Category",
            yaxis_title="Brand",
            height=350,
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig_cat, use_container_width=True)
        st.caption(
            "Darker = higher awareness in that category. Uniform dark rows = pan-category brands. "
            "Single dark cell = category specialist."
        )
    else:
        st.info("Category matrix unavailable — fact_brand_imagery category join returned no data.")

    st.divider()

    # Section: Appliance Ownership Story
    _section_title(
        "🏠 Category Penetration — What India Owns",
        "Kitchen appliance ownership across 6,631 households — market size context for brand battles",
    )
    appliance_df = get_appliance_ownership_story(project_id=project_id)
    if not appliance_df.empty:
        app_sorted = appliance_df.sort_values("penetration_pct", ascending=True)
        bar_colors = ["#6366f1" if p > 50 else "#8b5cf6" if p > 20 else "#d1d5db"
                      for p in app_sorted["penetration_pct"]]
        fig_app = go.Figure(go.Bar(
            y=app_sorted["appliance_name"].tolist(),
            x=app_sorted["penetration_pct"].tolist(),
            orientation="h",
            marker_color=bar_colors,
            text=[f"{p:.1f}%" for p in app_sorted["penetration_pct"]],
            textposition="outside",
        ))
        fig_app.update_layout(
            xaxis_title="% of Households",
            height=380,
            margin=dict(l=10, r=60, t=10, b=10),
            xaxis=dict(gridcolor="#f0f0f0", range=[0, 115]),
        )
        st.plotly_chart(fig_app, use_container_width=True)
        top_app = appliance_df.sort_values("penetration_pct", ascending=False).iloc[0]
        st.caption(
            f"**{top_app['appliance_name']}** leads at **{top_app['penetration_pct']:.1f}%** household penetration — "
            f"highest competition intensity. Brands fighting here face the most informed buyers."
        )
    else:
        st.info("Appliance ownership data unavailable.")

    st.divider()

    # Section: Qualitative Pulse
    _section_title(
        "💬 Qualitative Pulse — Consumer Voices",
        "Real verbatim responses linked to NPS scores — WHY consumers recommend or reject a brand",
    )
    verbatim_data = get_verbatim_pulse(project_id=project_id)
    if verbatim_data:
        sel_brand_v = st.selectbox(
            "Select brand to hear consumer voices",
            list(verbatim_data.keys()),
            key="dash_verbatim_brand",
        )
        vd = verbatim_data.get(sel_brand_v, {})
        v_col1, v_col2 = st.columns(2)
        with v_col1:
            st.markdown("**🟢 Promoter Voices** *(NPS 9-10 — why they recommend)*")
            for q in vd.get("promoter", [])[:3]:
                st.markdown(
                    f'<div style="background:#f0fdf4;border-left:3px solid #22c55e;'
                    f'padding:10px 14px;border-radius:6px;margin:6px 0;'
                    f'font-size:0.85rem;font-style:italic;color:#374151;">❝ {q} ❞</div>',
                    unsafe_allow_html=True,
                )
            if not vd.get("promoter"):
                st.info("No promoter verbatims for this brand.")
        with v_col2:
            st.markdown("**🔴 Detractor Voices** *(NPS 0-6 — why they would not recommend)*")
            for q in vd.get("detractor", [])[:3]:
                st.markdown(
                    f'<div style="background:#fef2f2;border-left:3px solid #ef4444;'
                    f'padding:10px 14px;border-radius:6px;margin:6px 0;'
                    f'font-size:0.85rem;font-style:italic;color:#374151;">❝ {q} ❞</div>',
                    unsafe_allow_html=True,
                )
            if not vd.get("detractor"):
                st.info("No detractor verbatims for this brand.")
        st.caption(
            "📊 These are real survey respondents who also gave NPS scores in the loyalty chart above. "
            "Promoter voice = amplify in messaging. Detractor voice = fix first."
        )
    else:
        st.info("Verbatim data unavailable.")

    st.divider()

    # Section: Hidden Hooks
    _section_title(
        "🪝 Hidden Hooks — AI-Discovered Brand Drivers",
        "Attributes with outsized NPS impact despite low stated importance — "
        "the real loyalty levers, often invisible in standard surveys",
    )
    hooks_df = get_hidden_hooks(project_id=project_id)
    if not hooks_df.empty:
        cats = hooks_df["category"].unique().tolist()
        sel_cat_h = st.selectbox("Filter by category", ["All"] + cats, key="dash_hooks_cat")
        filtered = hooks_df if sel_cat_h == "All" else hooks_df[hooks_df["category"] == sel_cat_h]
        cols_per_row = 3
        for i in range(0, len(filtered), cols_per_row):
            chunk = filtered.iloc[i:i+cols_per_row]
            hook_cols = st.columns(cols_per_row)
            for col, (_, hook) in zip(hook_cols, chunk.iterrows()):
                with col:
                    st.markdown(
                        f'<div style="border:1px solid rgba(99,102,241,0.3);border-radius:10px;'
                        f'padding:14px;background:linear-gradient(135deg,#faf5ff,#f0f9ff);'
                        f'margin-bottom:10px;min-height:150px;">'
                        f'<div style="font-size:0.62rem;font-weight:700;text-transform:uppercase;'
                        f'color:#6366f1;letter-spacing:0.05em;margin-bottom:6px;">'
                        f'{hook["category"]} · {hook["brand"]}</div>'
                        f'<div style="font-size:0.82rem;font-weight:600;color:#1e1b4b;margin-bottom:8px;line-height:1.3;">'
                        f'🪝 {hook["primary_driver"]}</div>'
                        f'<div style="font-size:0.72rem;color:#4b5563;line-height:1.4;">'
                        f'{str(hook["insight_narrative"])[:130]}...</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
        st.caption(
            "🔗 Connection to NPS: these hidden hooks explain why brands with similar awareness scores "
            "have very different NPS. The attribute that drives loyalty is often not the most stated one."
        )
    else:
        st.info("Hidden hooks not yet computed. Run brand driver analysis to populate.")

    st.divider()

    # Section: Recent Purchase Story
    _section_title(
        "🛒 What India Is Actually Buying — Recent Purchase",
        "Appliances most recently purchased by survey respondents — revealed demand, not stated intent",
    )
    rp_df = get_recent_purchase_story(project_id=project_id)
    if not rp_df.empty:
        rp_sorted = rp_df.sort_values("pct", ascending=True)
        bar_colors = ["#6366f1" if p == rp_sorted["pct"].max() else
                      "#8b5cf6" if p >= rp_sorted["pct"].quantile(0.6) else "#c4b5fd"
                      for p in rp_sorted["pct"]]
        fig_rp = go.Figure(go.Bar(
            y=rp_sorted["appliance_name"].tolist(),
            x=rp_sorted["pct"].tolist(),
            orientation="h",
            marker_color=bar_colors,
            text=[f"{p:.1f}%" for p in rp_sorted["pct"]],
            textposition="outside",
        ))
        fig_rp.update_layout(
            xaxis_title="% of Respondents Who Recently Purchased",
            height=360,
            margin=dict(l=10, r=70, t=10, b=10),
            xaxis=dict(gridcolor="#f0f0f0", range=[0, rp_sorted["pct"].max() * 1.18]),
        )
        st.plotly_chart(fig_rp, use_container_width=True)
        top = rp_df.sort_values("pct", ascending=False).iloc[0]
        st.caption(
            f"**{top['appliance_name']}** dominates recent purchases at **{top['pct']:.1f}%** of households. "
            "🔗 Cross-reference with Appliance Ownership above — high ownership + high recent purchase = replacement market, not first-time buyers."
        )
    else:
        st.info("Recent purchase data unavailable.")

    msg_count = len(st.session_state.get("messages", []))
    if msg_count > 0:
        st.markdown(
            f'<div style="margin-top:12px;padding:10px 16px;background:#f8fafc;border-radius:8px;'
            f'font-size:0.83rem;color:#6b7280;">'
            f"💬 {msg_count} messages in this session"
            f'</div>',
            unsafe_allow_html=True,
        )


show_dashboard()
