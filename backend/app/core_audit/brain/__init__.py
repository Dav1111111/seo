"""Studio v2 etap 7 — the brain.

Synthesises a «what to do this week» plan from the data the other
modules already produced. **No LLM in this pipeline** — every fact is
a SQL query, every action is a templated string with real values
substituted in. The owner trusts the plan because every line maps to a
specific row in the database.

Layers:
  - `snapshot`  : pure SQL, returns a dict of integers and small lists
  - `rules`     : pure Python, turns a snapshot into a list of Actions

The API endpoint composes both and returns the plan. Cost = $0,
latency < 500 ms.
"""

from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    build_snapshot,
)
from app.core_audit.brain.rules import (
    Action,
    Plan,
    build_plan,
)
from app.core_audit.brain.chat import chat_about_action
from app.core_audit.brain.battle_plan import battle_plan_result
from app.core_audit.brain.free_chat import free_chat

__all__ = [
    "BrainSnapshot",
    "build_snapshot",
    "Action",
    "Plan",
    "build_plan",
    "chat_about_action",
    "battle_plan_result",
    "free_chat",
]
