"""
Capability Registry
===================
All capability modules are registered here.

To add a new capability:
  1. Create skills/capabilities/your_capability.py
     (must define CAPABILITY_ID, DESCRIPTION, KEY_COLUMNS_SUMMARY, format_prompt())
  2. Import and add it to REGISTRY below.
  3. Add binding keys to config/project_N.CAPABILITIES["your_capability"].
  4. Add keyword list to config/project_N.KEYWORDS["your_capability"].
  5. Add "your_capability" to config/project_N.SKILL_PRIORITY at the right position.
  6. Document in docs/SKILLS.md.

That's it. No changes to foundry.py or views/chat.py.
"""

from infoleap.skills.capabilities import (
    awareness,
    nps,
    demographic,
    ownership,
    purchase,
    room,
)

# Maps capability_id → module. The foundry uses this to call format_prompt().
REGISTRY: dict = {
    awareness.CAPABILITY_ID:  awareness,
    nps.CAPABILITY_ID:        nps,
    demographic.CAPABILITY_ID: demographic,
    ownership.CAPABILITY_ID:  ownership,
    purchase.CAPABILITY_ID:   purchase,
    room.CAPABILITY_ID:       room,
}

__all__ = ["REGISTRY"]
