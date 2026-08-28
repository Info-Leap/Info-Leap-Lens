import os
def test_researcher_agent_prompt_has_radar():
    with open("oxdata/researcher_agent.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert '"type": "radar"' in content, "Prompt must instruct LLM on radar charts"
    assert '"type": "quadrant"' in content, "Prompt must instruct LLM on quadrant charts"
