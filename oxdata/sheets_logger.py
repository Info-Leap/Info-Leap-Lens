"""
Google Sheets Chat Logger (LENS 3.0 Deep-Audit)
==============================================
Expanded to capture statistical coefficients and strategic critiques.
"""

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import os
import json
from dotenv import load_dotenv

load_dotenv()

CREDS_PATH = os.environ.get("GOOGLE_SHEETS_CREDS", "credentials.json")
SPREADSHEET_NAME = "OxData_Chat_Logs"

# Expanded headers for LENS 3.0 deep analysis
HEADERS = [
    "timestamp", "session_id", "user_id", "project", "complexity",
    "question", "skill", "sql", "row_count", "latency_ms", 
    "r_squared", "top_drivers_json", "strategic_critique",
    "answer", "thinking", "error", "source", "raw_data_json"
]

def _get_worksheet():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDS_PATH, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_id = os.environ.get("SHEETS_LOG_ID", "1X_1cxiTeW5cdBzvjGiJzLvPBaNkYR-8AoqqwAnsqXdg")
        sh = client.open_by_key(sheet_id)
        return sh.worksheet("chat_log")
    except Exception as e:
        print(f"[sheets_logger] Connection failed: {e}")
        return None

import threading

def log_interaction(**params):
    """Entry point for logging. Spawns a background thread to prevent UI blocking."""
    threading.Thread(target=_log_task, kwargs=params, daemon=True).start()

def _log_task(**params):
    if not params: return
    try:
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ts_str = ist_now.strftime("%Y-%m-%d %H:%M:%S")

        df = params.get("df")
        row = [
            ts_str,
            str(params.get("session_id", "na")),
            str(params.get("user_id", "karpathy_audit_agent")),
            str(params.get("project_id", "project_1")),
            str(params.get("complexity", "complex")),
            str(params.get("question", ""))[:500],
            str(params.get("skill", "")),
            str(params.get("sql", ""))[:1000],
            len(df) if isinstance(df, pd.DataFrame) else 0,
            int(params.get("latency_ms", 0)),
            str(params.get("r_squared", "0.0")),
            str(params.get("top_drivers_json", ""))[:1500],
            str(params.get("synthesis", ""))[:2000],
            str(params.get("answer", ""))[:2000],
            str(params.get("reasoning", ""))[:2000],
            str(params.get("error", ""))[:500],
            str(params.get("source", "LENS_3.0_AUDIT")),
            (df.head(10).to_json(orient="records") if isinstance(df, pd.DataFrame) else "")[:5000]
        ]
        ws = _get_worksheet()
        if ws:
            ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[sheets_logger] Logging failed: {e}")

def setup_sheet_headers():
    ws = _get_worksheet()
    if ws:
        ws.clear()
        ws.append_row(HEADERS)
        print("[sheets_logger] Headers reset for LENS 3.0 Audit.")
        return True
    return False

if __name__ == "__main__":
    setup_sheet_headers()
