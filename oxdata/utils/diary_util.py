import os
from datetime import datetime, timedelta

DIARY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "diary.md")

def record_thought(tag: str, content: str):
    """Appends a timestamped entry to diary.md."""
    try:
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        ts = ist_now.strftime("%H:%M")
        
        entry = f"\n### [{ts} IST] - {tag}\n{content}\n"
        
        with open(DIARY_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"Failed to write to diary: {e}")

if __name__ == "__main__":
    # Test call
    record_thought("System Pulse", "Diary utility initialized and heartbeat detected.")
