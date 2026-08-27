import streamlit as st

def render_formula_reference():
    st.title("📚 Formula & BQ Mapping Reference")
    
    st.markdown("""
    This page provides a detailed reference for the quantitative metrics and BigQuery (BQ) variable mappings used in LENS.
    """)
    
    with st.expander("📊 Brand Funnel Mappings (BQ1 Series)", expanded=True):
        st.markdown("""
        The Brand Funnel tracks the consumer journey from awareness to purchase.
        
        | Stage | BQ Variable | Description |
        | :--- | :--- | :--- |
        | **TOM Awareness** | `bq1a` | Top-of-Mind (First brand mentioned) |
        | **Spontaneous** | `bq1b` | Brands mentioned without aid (includes TOM) |
        | **Aided Awareness** | `bq1c` | Brands recognized from a list |
        | **Ever Used** | `bq1d` | Brands the consumer has ever used |
        | **Current Use** | `bq1e` | Brands the consumer currently uses |
        | **Consideration** | `bq1f` | Brands the consumer would consider for next purchase |
        | **Preference** | `bq1g` | Most preferred brand |
        | **Recent Purchase**| `bq1h` | Brand purchased in the last 6 months |
        """)

    with st.expander("📈 Conversion Rate Formulas", expanded=True):
        st.markdown("""
        Conversion rates measure the efficiency of moving consumers between funnel stages.
        
        | Conversion Metric | Formula | Insight |
        | :--- | :--- | :--- |
        | **Awareness to Trial** | `Ever Used / Aided Awareness` | Measures trial efficiency / brand appeal. |
        | **Retention Rate** | `Current Use / Ever Used` | Measures loyalty and product satisfaction. |
        | **Consideration Set** | `Consideration / Aided Awareness`| Measures brand relevance in the category. |
        | **Close Rate** | `Recent Purchase / Preference` | Measures ability to convert intent to action. |
        """)

    with st.expander("🧠 Brand Imagery & Associations (BQ3 Series)", expanded=True):
        st.markdown("""
        Brand imagery is measured using attributes across different feature buckets.
        
        - **BQ3a**: Importance of attributes (1-7 scale).
        - **BQ3b**: Brand association with attributes (Binary).
        
        **Feature Buckets:**
        - **Product Performance:** Reliability, Durability, Power.
        - **Design & Aesthetics:** Looks, Color, Space-saving.
        - **After Sales Support:** Warranty, Service center availability.
        - **Value for Money:** Pricing vs. Features.
        """)

    with st.expander("✨ Brand Health Indicators", expanded=True):
        st.markdown("""
        - **NPS (Net Promoter Score) Proxy**: Calculated from `bq2b` (Recommendation Likelihood, 0-10 scale).
            - *Formula*: `% Promoters (9-10) - % Detractors (0-6)`.
        - **Momentum**: The % change in Aided Awareness or Current Use over a rolling timeframe (3m/6m).
        - **Leaky Bucket**: Visualizes the drop-off from *Aided Awareness* to *Ever Used* to *Current Use*.
        """)

    st.info("Note: All data is computed in real-time from the SQLite `lens.db` based on current global filters.")
