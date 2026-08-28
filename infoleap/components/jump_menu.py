import streamlit as st

@st.dialog("Jump to...", width="large")
def show_jump_menu():
    # Search Input
    st.text_input("Search or type a command...", placeholder="Search...", label_visibility="collapsed")
    
    st.markdown("---")
    
    # GO TO Section
    st.markdown("### GO TO")
    cols = st.columns(3)
    with cols[0]:
        if st.button("🏠 Home", use_container_width=True): st.switch_page("views/dashboard.py")
        if st.button("📚 Repository", use_container_width=True): st.switch_page("views/schema.py")
    with cols[1]:
        if st.button("📈 Brand Health", use_container_width=True): st.switch_page("views/brand_health.py")
    with cols[2]:
        if st.button("🗣️ Quote Explorer", use_container_width=True): st.switch_page("views/quote_explorer.py")

    st.markdown("### SUBJECTS")
    st.button("🟢 Mixer Grinder (Active)", use_container_width=True, disabled=True)
