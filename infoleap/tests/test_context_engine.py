import pytest
from unittest.mock import MagicMock
import sys
from pathlib import Path

# Add the project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Mock streamlit before importing ContextEngine
import streamlit as st

class SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)
    def __setattr__(self, key, value):
        self[key] = value

st.session_state = SessionState()

from infoleap.utils.context import ContextEngine

def test_context_engine_init():
    st.session_state.clear()
    ContextEngine.init()
    assert st.session_state.active_category == "Ceiling Fans"
    assert st.session_state.active_brand == "All Brands"
    assert st.session_state.active_zone == "Global"

def test_context_engine_set_context():
    st.session_state.clear()
    st.session_state.active_category = "Initial"
    st.session_state.active_brand = "Initial"
    st.session_state.active_zone = "Initial"
    
    ContextEngine.set_context(category="New Cat", brand="New Brand", zone="New Zone")
    assert st.session_state.active_category == "New Cat"
    assert st.session_state.active_brand == "New Brand"
    assert st.session_state.active_zone == "New Zone"

def test_context_engine_get_context():
    st.session_state.clear()
    st.session_state.active_category = "Cat"
    st.session_state.active_brand = "Brand"
    st.session_state.active_zone = "Zone"
    
    context = ContextEngine.get_context()
    assert context == {
        "category": "Cat",
        "brand": "Brand",
        "zone": "Zone"
    }
