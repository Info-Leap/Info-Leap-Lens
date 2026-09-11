import time
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

conv_file = Path(".agents/CONVERSATION.md").resolve()
initial_size = conv_file.stat().st_size if conv_file.exists() else 0
initial_lines = len(conv_file.read_text(encoding="utf-8").splitlines()) if conv_file.exists() else 0

print(f"Watching {conv_file} starting from line {initial_lines} (size: {initial_size} bytes)...")

while True:
    time.sleep(3)
    if not conv_file.exists():
        continue
    curr_size = conv_file.stat().st_size
    if curr_size != initial_size:
        text = conv_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > initial_lines:
            new_content = "\n".join(lines[initial_lines:])
            if "[CLAUDE]" in new_content:
                print("=== NEW CLAUDE MESSAGE DETECTED ===")
                print(new_content)
                break
