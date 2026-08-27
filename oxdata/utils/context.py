import streamlit as st

class ContextEngine:
    @staticmethod
    def init():
        if "active_category" not in st.session_state:
            st.session_state.active_category = "All"
        if "active_brand" not in st.session_state:
            st.session_state.active_brand = "All Brands"
        if "active_zone" not in st.session_state:
            st.session_state.active_zone = "Global"

    @staticmethod
    def set_context(category=None, brand=None, zone=None):
        if category: st.session_state.active_category = category
        if brand: st.session_state.active_brand = brand
        if zone: st.session_state.active_zone = zone

    @staticmethod
    def get_context():
        return {
            "category": st.session_state.active_category,
            "brand": st.session_state.active_brand,
            "zone": st.session_state.active_zone
        }
