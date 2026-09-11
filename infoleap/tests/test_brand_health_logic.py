import pytest
import streamlit as st
from unittest.mock import patch, MagicMock
import pandas as pd

# Mocking streamlit session state
if 'active_category' not in st.session_state:
    st.session_state.active_category = "Ceiling Fans"

def test_brand_health_category_filter():
    # We want to verify that get_brand_health_data (or equivalent) uses active_category
    from infoleap.views.brand_health import get_brand_health_data
    
    with patch('sqlite3.connect') as mock_connect:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        # Mocking pandas read_sql to return dummy data
        mock_df_resp = pd.DataFrame({'n': [100]})
        mock_df_aw = pd.DataFrame({'brand_name': ['B1'], 'pct': [10.0]})
        mock_df_use = pd.DataFrame({'brand_name': ['B1'], 'pct': [5.0]})
        
        mock_conn.execute.return_value.fetchone.return_value = [100]
        # In brand_health.py, it uses pd.read_sql
        
        with patch('pandas.read_sql') as mock_read_sql:
            mock_read_sql.side_effect = [mock_df_resp, mock_df_aw, mock_df_use]
            
            st.session_state.active_category = "Mixer Grinder"
            get_brand_health_data()
            
            # Check if active_category was used in SQL
            calls = mock_read_sql.call_args_list
            for call in calls:
                sql = call[0][0]
                # After our changes, it should contain a WHERE category = ? or similar
                # For now it doesn't, so this test should fail once we add the expectation
                assert "WHERE category =" in sql
