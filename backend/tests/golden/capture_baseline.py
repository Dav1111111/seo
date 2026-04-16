"""Capture current output of pure (DB-free) intent functions as baseline.json.

Run ONCE before starting the core/profile refactor. After each refactor step,
`test_parity.py` replays the fixtures and diffs against this baseline.

DB-dependent checks (c5_long_term_demand, DecisionTree.decide, Decisioner)
are verified on prod via API diff after Step 10 — they cannot be snapshotted
without a seeded database.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from app.intent.classifier import classify_query
from app.intent.enums import IntentCode
from app.intent.page_classifier import score_page_all_intents
from app.intent.standalone_test import (
    check_c1_unique_entity,
    check_c2_irreducible_content,
    check_c3_distinct_user_task,
    check_c4_distinct_serp,
)

from tests.golden.fixtures import SAMPLE_PAGES, SAMPLE_QUERIES, SAMPLE_STANDALONE

BASELINE_PATH = Path(__file__).parent / "baseline.json"


def _serialize(obj):
    if is_dataclass(obj):
        d = asdict(obj)
        return {k: _serialize(v) for k, v in d.items()}
    if isinstance(obj, IntentCode):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, tuple):
        return [_serialize(v) for v in obj]
    return obj


def capture_queries() -> list[dict]:
    out = []
    for q, brands in SAMPLE_QUERIES:
        r = classify_query(q, known_brands=brands)
        out.append({
            "query": q,
            "known_brands": brands,
            "intent": r.intent.value,
            "confidence": round(r.confidence, 4),
            "matched_pattern": r.matched_pattern,
            "is_brand": r.is_brand,
            "is_ambiguous": r.is_ambiguous,
        })
    return out


def capture_pages() -> list[dict]:
    out = []
    for p in SAMPLE_PAGES:
        scores = score_page_all_intents(
            path=p["path"],
            title=p["title"],
            h1=p["h1"],
            content_text=p["content_text"],
            word_count=p["word_count"],
            has_schema=p["has_schema"],
            images_count=p["images_count"],
        )
        scores_serialized = {
            intent.value: {
                "score": s.score,
                "s1": s.s1_heading, "s2": s.s2_content, "s3": s.s3_structure,
                "s4": s.s4_cta, "s5": s.s5_schema, "s6": s.s6_eeat,
            }
            for intent, s in scores.items()
        }
        out.append({"name": p["name"], "scores": scores_serialized})
    return out


def capture_standalone() -> list[dict]:
    out = []
    for s in SAMPLE_STANDALONE:
        proposed = IntentCode(s["proposed_intent"])
        parent = IntentCode(s["parent_intent"]) if s["parent_intent"] else None

        c1_bool, c1_reason = check_c1_unique_entity(s["proposed_title"], s["proposed_query"])
        c2_val, c2_reason = check_c2_irreducible_content(proposed)
        c3_bool, c3_reason = check_c3_distinct_user_task(proposed, parent)
        c4_val, c4_reason = check_c4_distinct_serp()

        out.append({
            "name": s["name"],
            "c1": {"pass": c1_bool, "reason": c1_reason},
            "c2": {"pass": c2_val, "reason": c2_reason},
            "c3": {"pass": c3_bool, "reason": c3_reason},
            "c4": {"pass": c4_val, "reason": c4_reason},
        })
    return out


def main() -> None:
    baseline = {
        "schema_version": 1,
        "queries": capture_queries(),
        "pages": capture_pages(),
        "standalone": capture_standalone(),
    }
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Baseline written: {BASELINE_PATH}")
    print(f"  queries:    {len(baseline['queries'])}")
    print(f"  pages:      {len(baseline['pages'])}")
    print(f"  standalone: {len(baseline['standalone'])}")


if __name__ == "__main__":
    main()
