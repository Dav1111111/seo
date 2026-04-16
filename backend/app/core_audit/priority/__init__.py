"""Module 4 — Prioritization.

Scores each PageReviewRecommendation on Impact × Confidence × Ease and
aggregates top-N lists per site. Scores persisted as columns on the
recommendation row (computed once per review run, cheap on read).

  scorer.py       — pure scoring function (rec + context → ScoreBreakdown)
  constants.py    — category/signal weight + ease-minutes maps
  dto.py          — PrioritizedItem, WeeklyPlan, ScoreBreakdown
  aggregator.py   — site ranking + weekly_plan diversification
  service.py      — PriorityService async orchestration + DB reads
  tasks.py        — Celery rescore_site + rescore_recommendations
"""

from app.core_audit.priority.dto import (
    PrioritizedItem,
    ScoreBreakdown,
    WeeklyPlan,
)
from app.core_audit.priority.scorer import SCORER_VERSION, score_recommendation

__all__ = [
    "PrioritizedItem",
    "SCORER_VERSION",
    "ScoreBreakdown",
    "WeeklyPlan",
    "score_recommendation",
]
