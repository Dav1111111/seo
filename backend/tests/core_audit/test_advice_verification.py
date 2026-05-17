"""Tests for advice card auto-verification (Point 1 of the roadmap).

Most verifiers depend on DB state (deep_extracts, robots audit cache,
metrica counter, search_queries) so the heavy integration coverage lives
in the per-module tests. Here we test the dispatcher routing + the
contract of `VerificationResult`.
"""

from __future__ import annotations

import pytest

from app.core_audit.advisor.verification.dispatcher import (
    VERIFICATION_STATUSES, VerificationResult, _user_attested,
)


# ── DTO contract ────────────────────────────────────────────────────


def test_verification_result_rejects_unknown_status():
    """Invalid status must be caught at construction, not at write time."""
    with pytest.raises(ValueError):
        VerificationResult(
            status="totally_invented", evidence={}, message_ru="x",
        )


def test_verification_result_accepts_all_valid_statuses():
    for s in VERIFICATION_STATUSES:
        r = VerificationResult(status=s, evidence={}, message_ru="ok")
        assert r.status == s


def test_verification_statuses_match_spec():
    """Frozen contract — frontend reads these literal strings."""
    assert set(VERIFICATION_STATUSES) == {
        "verified", "not_yet_visible", "user_attested", "failed",
    }


def test_user_attested_fallback_carries_reason():
    r = _user_attested("category foo has no verifier")
    assert r.status == "user_attested"
    assert r.evidence["reason"] == "category foo has no verifier"
    assert "принимаем на слово" in r.message_ru


# ── Dispatcher routing — sanity, no DB ──────────────────────────────


def test_canonical_schema_type_map_covers_common_money_types():
    """Owner-facing copy depends on TitleCase: «Не хватает FAQPage»
    must not become «Не хватает Faqpage»."""
    from app.core_audit.advisor.verification.verifiers import (
        _SCHEMA_TYPE_CANONICAL,
    )
    must_have = {
        "faqpage": "FAQPage",
        "touristtrip": "TouristTrip",
        "offer": "Offer",
        "product": "Product",
        "breadcrumblist": "BreadcrumbList",
        "aggregateoffer": "AggregateOffer",
    }
    for raw, canonical in must_have.items():
        assert _SCHEMA_TYPE_CANONICAL[raw] == canonical


def test_normalize_schema_types_strips_url_prefix():
    """`@type` from JSON-LD is sometimes a full URL — must strip
    schema.org prefix so the verifier can compare against
    canonical TitleCase names."""
    from app.core_audit.advisor.verification.verifiers import (
        _normalize_schema_types,
    )
    blocks = [
        {"@type": "http://schema.org/FAQPage"},
        {"@type": "https://schema.org/Offer"},
        {"@type": ["TouristTrip", "Service"]},
        {"@type": "BreadcrumbList"},
        {"@type": None},  # malformed — must not crash
        "not-a-dict",  # noise — must not crash
        None,  # ditto
    ]
    assert _normalize_schema_types(blocks) == {
        "FAQPage", "Offer", "TouristTrip", "Service", "BreadcrumbList",
    }


def test_page_id_from_link_parses_studio_link():
    """Card.link shape is `/studio/pages/{uuid}` — parser must
    handle trailing query string and slashes gracefully."""
    from app.core_audit.advisor.verification.verifiers import (
        _page_id_from_link,
    )
    pid = _page_id_from_link("/studio/pages/0437133b-cd61-469c-b6fd-b468d5614a8a")
    assert str(pid) == "0437133b-cd61-469c-b6fd-b468d5614a8a"

    pid2 = _page_id_from_link(
        "/studio/pages/0437133b-cd61-469c-b6fd-b468d5614a8a/?ref=advice"
    )
    assert str(pid2) == "0437133b-cd61-469c-b6fd-b468d5614a8a"

    assert _page_id_from_link(None) is None
    assert _page_id_from_link("https://example.com/abc") is None
    assert _page_id_from_link("/studio/queries") is None
    assert _page_id_from_link("/studio/pages/not-a-uuid") is None


# ── Beat task contract ──────────────────────────────────────────────


def test_celery_tasks_registered():
    """Both tasks (per-card verify + daily sweep) must be importable
    via their registered Celery name — beat would silently no-op if
    the name didn't match the @task decorator argument."""
    from app.workers.celery_app import celery_app
    registered = set(celery_app.tasks.keys())
    assert "verify_advice_card_application" in registered
    assert "verify_unverified_daily" in registered


def test_beat_schedule_has_daily_verify_sweep():
    """The daily sweep must be on the beat schedule, else cards stuck
    in `not_yet_visible` will never get rechecked after a late deploy."""
    from app.workers.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "verify-unverified-daily" in schedule
    assert schedule["verify-unverified-daily"]["task"] == "verify_unverified_daily"
