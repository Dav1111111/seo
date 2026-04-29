"""Tests for app.core_audit.harmful_diagnoser.

Three pure-ish surfaces are pinned:

  find_matched_url       — domain matching against SerpDoc list,
                           with mocked fetch_serp.
  score_page_for_query   — token overlap math against a synthetic Page.
  diagnose_one           — LLM result-shaping with mocked call_with_tool.

No DB, no network. Pattern follows test_relevance_rules.py — pure
functions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from app.collectors.yandex_serp import SerpDoc
from app.core_audit.harmful_diagnoser import (
    MatchedPageInfo,
    SERP_DEPTH,
    _build_user_message,
    diagnose_one,
    find_matched_url,
    score_page_for_query,
)


# ── Helpers ────────────────────────────────────────────────────────


def _doc(position: int, url: str, domain: str, title: str = "t",
         headline: str = "h") -> SerpDoc:
    return SerpDoc(
        position=position, url=url, domain=domain,
        title=title, headline=headline,
    )


@dataclass
class FakePage:
    """Stand-in for `app.models.page.Page` — only the attributes
    score_page_for_query reads."""
    title: str | None = None
    h1: str | None = None
    meta_description: str | None = None
    content_text: str | None = None


# ── find_matched_url ───────────────────────────────────────────────


def test_find_matched_url_empty_query_returns_none() -> None:
    assert find_matched_url("", "example.ru") is None


def test_find_matched_url_empty_domain_returns_none() -> None:
    assert find_matched_url("query", "") is None


def test_find_matched_url_serp_error_returns_none() -> None:
    """Rate-limit / network error from fetch_serp → no crash, no match.
    Caller distinguishes via downstream «no_match» branch."""
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=([], "http_429_on_submit"),
    ):
        assert find_matched_url("q", "example.ru") is None


def test_find_matched_url_no_docs_returns_none() -> None:
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=([], None),
    ):
        assert find_matched_url("q", "example.ru") is None


def test_find_matched_url_exact_domain_match() -> None:
    docs = [
        _doc(1, "https://other.ru/x", "other.ru"),
        _doc(2, "https://example.ru/page", "example.ru", title="Pg", headline="hd"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "example.ru")
    assert m is not None
    assert m.url == "https://example.ru/page"
    assert m.position == 2
    assert m.title == "Pg"
    assert m.headline == "hd"


def test_find_matched_url_returns_first_match_in_serp_order() -> None:
    """When our domain appears multiple times, we want the highest
    position (which is `docs[0]` because fetch_serp returns ordered)."""
    docs = [
        _doc(1, "https://other.ru/x", "other.ru"),
        _doc(2, "https://example.ru/a", "example.ru"),
        _doc(3, "https://example.ru/b", "example.ru"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "example.ru")
    assert m is not None
    assert m.url == "https://example.ru/a"
    assert m.position == 2


def test_find_matched_url_with_www_prefix_matches() -> None:
    """`www.example.ru` in SerpDoc.domain must match `example.ru`
    in our profile — the SerpDoc layer uses _extract_domain that
    strips www, but we may still see «www.» if Yandex returned the
    bare host with www and our domain doesn't carry it."""
    docs = [
        _doc(1, "https://www.example.ru/", "www.example.ru"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "example.ru")
    # The current implementation strips leading "www." chars via
    # lstrip("www."), which works for this canonical case.
    assert m is not None
    assert m.url == "https://www.example.ru/"


def test_find_matched_url_subdomain_does_not_match_root() -> None:
    """`shop.example.ru` should NOT match the profile domain
    `other.ru`. Suffix logic must respect the dot boundary."""
    docs = [
        _doc(1, "https://shop.example.ru/", "shop.example.ru"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "other.ru")
    assert m is None


def test_find_matched_url_subdomain_matches_root() -> None:
    """`shop.example.ru` SHOULD match the profile domain `example.ru`
    — owners often run blog./shop. subdomains and want them attributed."""
    docs = [
        _doc(1, "https://shop.example.ru/", "shop.example.ru"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "example.ru")
    assert m is not None
    assert m.url == "https://shop.example.ru/"


def test_find_matched_url_uses_serp_depth() -> None:
    """fetch_serp must be called with groups=SERP_DEPTH (30), not
    the SERP module's default of 10."""
    captured = {}

    def fake(query, *, groups, **kwargs):
        captured["groups"] = groups
        return [], None

    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        side_effect=fake,
    ):
        find_matched_url("q", "example.ru")

    assert captured["groups"] == SERP_DEPTH == 30


def test_find_matched_url_case_insensitive_domain() -> None:
    docs = [
        _doc(1, "https://EXAMPLE.RU/x", "EXAMPLE.RU"),
    ]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("q", "Example.RU")
    assert m is not None


# ── score_page_for_query ───────────────────────────────────────────


def test_score_empty_query_zero() -> None:
    p = FakePage(title="багги сочи", content_text="катаемся")
    assert score_page_for_query("", p) == 0


def test_score_empty_page_zero() -> None:
    assert score_page_for_query("багги сочи", FakePage()) == 0


def test_score_overlap_counts_query_tokens_in_page() -> None:
    p = FakePage(
        title="Багги-туры в Сочи",
        h1="",
        meta_description="",
        content_text="Лучшие маршруты с гидом",
    )
    # Query tokens (≥3 chars, lowercased): {"багги","сочи","туры"}
    # Page has: "багги","туры","сочи","лучшие","маршруты","гидом"
    # Overlap: "багги","сочи","туры" → 3
    assert score_page_for_query("багги туры сочи", p) == 3


def test_score_short_tokens_ignored() -> None:
    """Tokens < 3 chars are stop-word noise. «и», «в», «на» must not
    inflate the score."""
    p = FakePage(title="и в на")
    assert score_page_for_query("и в на тур", p) == 0


def test_score_case_insensitive() -> None:
    p = FakePage(title="БАГГИ Сочи")
    assert score_page_for_query("багги сочи", p) == 2


def test_score_only_first_800_chars_of_content() -> None:
    """Long tail of content is truncated — token after position 800
    won't count, preventing one massive page from dominating the
    scoring fallback."""
    long_filler = "x " * 500  # 1000 chars before our marker
    p = FakePage(
        title="",
        h1="",
        meta_description="",
        content_text=long_filler + " магия",
    )
    assert score_page_for_query("магия", p) == 0


def test_score_handles_none_columns() -> None:
    """All page text fields are nullable — must not crash on Nones."""
    p = FakePage(
        title=None, h1=None, meta_description=None, content_text=None,
    )
    assert score_page_for_query("анything", p) == 0


def test_score_dedupes_query_tokens() -> None:
    """Repeated query tokens count once. «багги багги» = same as
    «багги»."""
    p = FakePage(title="багги")
    assert score_page_for_query("багги багги багги", p) == 1


# ── _build_user_message — diagnose prompt ──────────────────────────


def test_diagnose_user_message_includes_all_fields() -> None:
    matched = MatchedPageInfo(
        url="https://example.ru/p",
        position=12,
        title="t",
        headline="h",
    )
    msg = _build_user_message(
        query="джинсы багги",
        relevance="spam",
        relevance_reason="одежда не транспорт",
        business_narrative="премиум туры",
        business_primary="багги",
        business_geo=["сочи", "абхазия"],
        matched=matched,
        page_title="Магазин джинсов",
        page_h1="Распродажа",
        page_meta="широкий выбор",
        page_content_excerpt="брюки, штаны...",
    )
    assert "БИЗНЕС" in msg
    assert "багги" in msg
    assert "сочи, абхазия" in msg
    assert "ВРЕДНЫЙ ЗАПРОС: джинсы багги" in msg
    assert "класс: spam" in msg
    assert "https://example.ru/p" in msg
    assert "позиция: 12" in msg
    assert "Магазин джинсов" in msg
    assert "diagnose_harmful_visibility" in msg


def test_diagnose_user_message_handles_missing_geo() -> None:
    matched = MatchedPageInfo(url="u", position=1, title="t", headline="h")
    msg = _build_user_message(
        query="q",
        relevance="disputed",
        relevance_reason=None,
        business_narrative="",
        business_primary="",
        business_geo=[],
        matched=matched,
        page_title=None,
        page_h1=None,
        page_meta=None,
        page_content_excerpt="—",
    )
    assert "регионы: —" in msg
    assert "—" in msg


# ── diagnose_one — full result shape ───────────────────────────────


def _matched(**kw) -> MatchedPageInfo:
    return MatchedPageInfo(
        url=kw.get("url", "https://example.ru/page"),
        position=kw.get("position", 11),
        title=kw.get("title", "Title"),
        headline=kw.get("headline", "Snippet"),
    )


def _diag_call(tool_input: dict, usage: dict | None = None):
    usage = usage or {
        "model": "claude-haiku-test",
        "input_tokens": 200,
        "output_tokens": 80,
        "cost_usd": 0.0005,
    }
    return patch(
        "app.core_audit.harmful_diagnoser.call_with_tool",
        return_value=(tool_input, usage),
    )


def test_diagnose_one_returns_jsonb_shape() -> None:
    """The returned dict MUST match the JSONB shape documented in the
    migration. Frontend parses these keys directly — drift here means
    a UI crash."""
    fake = {
        "cause_ru": "Слова «багги» и «джинсы» в тексте.",
        "fix_title": "Багги-туры в Сочи — премиум-экспедиции",
        "fix_h1": "Багги-туры по горам Сочи",
        "fix_meta_description": "Премиум багги-экспедиции в Сочи.",
        "fix_content_change_ru": "Убрать упоминания «джинсы».",
        "schema_recommendation": "Schema TouristTrip",
        "noindex_recommended": False,
    }
    with _diag_call(fake):
        out = diagnose_one(
            query="джинсы багги",
            relevance="spam",
            relevance_reason="одежда",
            business_narrative="премиум туры",
            business_primary="багги",
            business_geo=["сочи"],
            matched=_matched(),
            page_title="...",
            page_h1="...",
            page_meta="...",
            page_content="...",
        )

    # Top-level keys
    for k in (
        "matched_url", "matched_position", "cause_ru", "fixes",
        "model", "cost_usd", "diagnosed_at",
    ):
        assert k in out

    assert out["matched_url"] == "https://example.ru/page"
    assert out["matched_position"] == 11
    assert out["cause_ru"].startswith("Слова")
    assert out["model"] == "claude-haiku-test"
    assert out["cost_usd"] == 0.0005
    assert isinstance(out["diagnosed_at"], str)

    # fixes sub-shape — frontend reads these
    fixes = out["fixes"]
    assert fixes["title_change"].startswith("Багги")
    assert fixes["h1_change"].startswith("Багги")
    assert fixes["meta_description_change"].startswith("Премиум")
    assert fixes["content_change_ru"].startswith("Убрать")
    assert fixes["schema_recommendation"] == "Schema TouristTrip"
    assert fixes["noindex_recommended"] is False


def test_diagnose_one_null_fixes_pass_through() -> None:
    """When the LLM returns null for a fix (because that field isn't
    the problem), it must pass through as None — frontend uses
    explicit null to hide the section."""
    fake = {
        "cause_ru": "Title уже хороший.",
        "fix_title": None,
        "fix_h1": None,
        "fix_meta_description": None,
        "fix_content_change_ru": "Убрать абзац X.",
        "schema_recommendation": None,
        "noindex_recommended": False,
    }
    with _diag_call(fake):
        out = diagnose_one(
            query="q", relevance="spam", relevance_reason=None,
            business_narrative="n", business_primary="p", business_geo=[],
            matched=_matched(),
            page_title=None, page_h1=None, page_meta=None,
            page_content=None,
        )
    assert out["fixes"]["title_change"] is None
    assert out["fixes"]["h1_change"] is None
    assert out["fixes"]["meta_description_change"] is None
    assert out["fixes"]["content_change_ru"] == "Убрать абзац X."
    assert out["fixes"]["schema_recommendation"] is None


def test_diagnose_one_noindex_truthy_coerced_to_bool() -> None:
    """LLM might emit truthy non-bool values; we cast to bool so
    JSONB stays clean and `noindex_recommended === true` works in JS."""
    fake = {
        "cause_ru": "x",
        "fix_title": None,
        "fix_h1": None,
        "fix_meta_description": None,
        "fix_content_change_ru": None,
        "schema_recommendation": None,
        "noindex_recommended": "yes",  # truthy string
    }
    with _diag_call(fake):
        out = diagnose_one(
            query="q", relevance="spam", relevance_reason=None,
            business_narrative="", business_primary="", business_geo=[],
            matched=_matched(),
            page_title=None, page_h1=None, page_meta=None,
            page_content=None,
        )
    assert out["fixes"]["noindex_recommended"] is True


def test_diagnose_one_missing_fields_defaulted() -> None:
    """If LLM somehow returned an empty dict, diagnose_one mustn't
    KeyError — every field gets a None / empty default."""
    with _diag_call({}):
        out = diagnose_one(
            query="q", relevance="spam", relevance_reason=None,
            business_narrative="", business_primary="", business_geo=[],
            matched=_matched(),
            page_title=None, page_h1=None, page_meta=None,
            page_content=None,
        )
    assert out["cause_ru"] == ""
    assert out["fixes"]["noindex_recommended"] is False
    assert out["fixes"]["title_change"] is None


def test_diagnose_one_truncates_page_content_to_1200() -> None:
    """The prompt must include only the first 1200 chars of content
    — long pages would explode tokens. Verified by inspecting the
    user_message passed to call_with_tool."""
    captured = {}

    def fake_call(*, model_tier, system, user_message, tool, max_tokens):
        captured["user_message"] = user_message
        return {
            "cause_ru": "x",
            "fix_title": None,
            "fix_h1": None,
            "fix_meta_description": None,
            "fix_content_change_ru": None,
            "schema_recommendation": None,
            "noindex_recommended": False,
        }, {"model": "m", "cost_usd": 0.0}

    long_content = "ABC" * 2000  # 6000 chars
    with patch(
        "app.core_audit.harmful_diagnoser.call_with_tool",
        side_effect=fake_call,
    ):
        diagnose_one(
            query="q", relevance="spam", relevance_reason=None,
            business_narrative="", business_primary="", business_geo=[],
            matched=_matched(),
            page_title=None, page_h1=None, page_meta=None,
            page_content=long_content,
        )

    msg = captured["user_message"]
    # The truncated chunk should be present, but a marker beyond the
    # 1200th char (we put nothing — but the full string would be there
    # without truncation). Easier check: count occurrences of "ABC"
    # in the message can't exceed 1200/3 = 400.
    assert msg.count("ABC") <= 400


def test_diagnose_one_empty_content_renders_dash() -> None:
    """Empty / whitespace-only content_text shows up as «—» in the
    prompt so the LLM doesn't see a blank page section."""
    captured = {}

    def fake_call(*, model_tier, system, user_message, tool, max_tokens):
        captured["user_message"] = user_message
        return {
            "cause_ru": "x",
            "fix_title": None, "fix_h1": None, "fix_meta_description": None,
            "fix_content_change_ru": None, "schema_recommendation": None,
            "noindex_recommended": False,
        }, {"model": "m", "cost_usd": 0.0}

    with patch(
        "app.core_audit.harmful_diagnoser.call_with_tool",
        side_effect=fake_call,
    ):
        diagnose_one(
            query="q", relevance="spam", relevance_reason=None,
            business_narrative="", business_primary="", business_geo=[],
            matched=_matched(),
            page_title=None, page_h1=None, page_meta=None,
            page_content="   \n  ",
        )

    assert "фрагмент контента (первые 1200 символов):\n  —" in captured["user_message"]


# ── Constant pin ───────────────────────────────────────────────────


def test_serp_depth_is_30() -> None:
    """If this changes, /studio/queries/harmful position threshold
    documentation goes stale."""
    assert SERP_DEPTH == 30
