import streamlit as st

def show_error_card(title, message, icon="⚠️"):
    """Displays a professional InfoLeap-styled error card."""
    st.markdown(f"""
        <div class="pulse-card" style="border-left: 5px solid #ef4444; background-color: #fef2f2;">
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                <div style="font-size: 1.5rem;">{icon}</div>
                <div style="font-size: 1.1rem; font-weight: 700; color: #991b1b;">{title}</div>
            </div>
            <div style="font-size: 0.95rem; color: #b91c1c; line-height: 1.5;">
                {message}
            </div>
            <div style="margin-top: 12px; font-size: 0.8rem; color: #7f1d1d; opacity: 0.7;">
                If this persists, please contact the system administrator with the details above.
            </div>
        </div>
    """, unsafe_allow_html=True)

def show_data_unavailable(category, brand):
    """Specific fallback for missing data segments."""
    show_error_card(
        "Segment Data Unavailable",
        f"We couldn't find enough respondent data for <b>{brand}</b> in the <b>{category}</b> category with your current filters.",
        icon="📊"
    )
