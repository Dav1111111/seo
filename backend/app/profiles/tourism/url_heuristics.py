"""URL + Title proposer functions for tourism.

IMPORTANT — these mirror the old decision_tree._propose_title / _propose_url
byte-for-byte to preserve Decisioner output through the refactor. Upgraded
Yandex-optimized templates (Title ≤65 chars, proper format) will land in
Module 3 alongside LLM-generated rewrites.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode


def propose_title(intent: IntentCode, top_query: str) -> str:
    top_q = top_query or intent.value
    if intent is IntentCode.LOCAL_GEO:
        return f"Экскурсии с бесплатным трансфером — {top_q}"
    if intent is IntentCode.COMM_MODIFIED:
        return f"{top_q.capitalize()} — программа и цены"
    if intent is IntentCode.INFO_DEST:
        return f"Что посмотреть: {top_q}"
    if intent is IntentCode.INFO_LOGISTICS:
        return f"Как добраться: {top_q}"
    if intent is IntentCode.INFO_PREP:
        return f"Советы: {top_q}"
    if intent is IntentCode.COMM_COMPARE:
        return f"ТОП-10 вариантов: {top_q}"
    return top_q


def propose_url(intent: IntentCode, top_query: str) -> str:
    slug_base = {
        IntentCode.LOCAL_GEO: "/pickup/",
        IntentCode.COMM_MODIFIED: "/tours/",
        IntentCode.COMM_CATEGORY: "/tours",
        IntentCode.INFO_DEST: "/guide/",
        IntentCode.INFO_LOGISTICS: "/transport/",
        IntentCode.INFO_PREP: "/blog/",
        IntentCode.COMM_COMPARE: "/top/",
        IntentCode.TRUST_LEGAL: "/reviews",
    }.get(intent, "/")
    if top_query:
        slug = "-".join(top_query.lower().split()[:3])
        return f"{slug_base}{slug}" if slug_base.endswith("/") else f"{slug_base}-{slug}"
    return slug_base
