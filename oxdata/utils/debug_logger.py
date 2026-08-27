import json
import os
import pandas as pd
from datetime import datetime
from pathlib import Path

# Absolute path relative to this file's directory (utils/ -> oxdata/ -> data/debug_logs)
BASE_DIR = Path(__file__).parent.parent
DEBUG_LOG_DIR = BASE_DIR / "data" / "debug_logs"
DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)

def log_execution_triple(question, sql, df, chart_type, intent):
    """Saves the complete state of a query for bug fixing."""
    log_file = DEBUG_LOG_DIR / f"debug_{datetime.now().strftime('%Y%m%d')}.jsonl"
    
    # Convert DF to list of dicts for JSON
    data_sample = []
    if isinstance(df, pd.DataFrame):
        data_sample = df.head(50).to_dict(orient="records")
    
    entry = {
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "intent": intent,
        "sql": sql,
        "data_sample": data_sample,
        "chart_type": str(chart_type),
        "row_count": len(df) if df is not None else 0
    }
    
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
