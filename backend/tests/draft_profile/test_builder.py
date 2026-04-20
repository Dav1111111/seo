"""Integration-ish tests for the draft_profile builder.

The builder performs three DB reads (site, pages, queries) and one DB
write (update sites.target_config_draft). We mock the session so these
tests run without Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from app.core_audit.draft_profile.builder import build_draft_profile
from app.core_audit.draft_profile.dto import DraftProfile


@dataclass
class FakeSite:
    id: uuid.UUID
    display_name: str
    domain: str
    target_config: dict
    target_config_draft: dict


@dataclass
class FakePage:
    title: str | None = None
    h1: str | None = None
    content_text: str | None = None
    url: str | None = None
    path: str | None = None
    word_count: int | None = 0


@dataclass
class FakeQuery:
    query_text: str
    is_branded: bool = False


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, site, pages, queries):
        self.site = site
        self._pages = pages
        self._queries = queries
        self.updates: list = []
        self.get = AsyncMock(side_effect=self._get)
        self.flush = AsyncMock(return_value=None)

    async def _get(self, model, ident):
        return self.site

    async def execute(self, stmt):
        # Compile the statement to figure out target table / operation.
        s = str(stmt).lower()
        if s.startswith("update"):
            # Capture update values by calling the stmt's compile.
            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                self.updates.append(stmt)
            except Exception:
                self.updates.append(stmt)
            return FakeResult([])
        if "search_queries" in s:
            return FakeResult(self._queries)
        if "pages" in s:
            return FakeResult(self._pages)
        return FakeResult([])


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_build_draft_profile_happy_path_no_llm():
    site_id = uuid.uuid4()
    site = FakeSite(
        id=site_id,
        display_name="ЮК",
        domain="ukontinent.example",
        target_config={},
        target_config_draft={},
    )
    pages = [
        FakePage(title="Экскурсии в Сочи", h1="Сочи", content_text="яхты дайвинг"),
        FakePage(title="Туры Сочи и Адлер", h1="Адлер", content_text="яхты"),
        FakePage(title="Яхты Сочи", h1="Яхты", content_text="аренда яхт"),
    ]
    queries = [
        FakeQuery(query_text="экскурсии сочи"),
        FakeQuery(query_text="адлер туры"),
    ]
    db = FakeDB(site, pages, queries)

    # Injected LLM caller that always returns empty (fail-open path).
    def _empty_caller(**_kwargs):
        return ({"competitor_brands": []}, {"cost_usd": 0.0})

    profile = _run(build_draft_profile(
        db, site_id, competitor_caller=_empty_caller,
    ))

    assert isinstance(profile, DraftProfile)
    assert profile.site_id == site_id
    assert profile.draft_config["services"]
    # Yachts should survive threshold.
    assert "яхты" in profile.draft_config["services"]
    # Sochi is in primary or secondary (most pages mention it).
    geo_all = set(profile.draft_config["geo_primary"]) | set(
        profile.draft_config["geo_secondary"]
    )
    assert "сочи" in geo_all
    # Universals always present.
    assert "туры" in profile.draft_config["services"]
    assert "экскурсии" in profile.draft_config["services"]
    # No competitors since LLM returned empty.
    assert profile.draft_config["competitor_brands"] == []
    # Overall confidence is a valid number.
    assert 0.0 <= profile.overall_confidence <= 1.0
    # We wrote to DB exactly once via update().
    assert len(db.updates) == 1


def test_build_draft_profile_llm_error_is_fail_open():
    site_id = uuid.uuid4()
    site = FakeSite(
        id=site_id,
        display_name="GTS",
        domain="gts.example",
        target_config={},
        target_config_draft={},
    )
    pages = [FakePage(title="Сочи", h1="Сочи", content_text="багги джиппинг багги джиппинг")]
    queries = [FakeQuery(query_text="гтс официальный сайт", is_branded=True)]
    db = FakeDB(site, pages, queries)

    def _exploding_caller(**_kwargs):
        raise RuntimeError("anthropic down")

    # Should not raise.
    profile = _run(build_draft_profile(
        db, site_id, competitor_caller=_exploding_caller,
    ))
    assert profile.draft_config["competitor_brands"] == []
    assert profile.signals["pages_analyzed"] == 1
    assert profile.signals["queries_analyzed"] == 1


def test_build_draft_profile_raises_on_missing_site():
    site_id = uuid.uuid4()
    db = FakeDB(None, [], [])

    with pytest.raises(LookupError):
        _run(build_draft_profile(db, site_id, competitor_caller=lambda **_: ({}, {})))


def test_build_draft_profile_returns_target_config_shape():
    site_id = uuid.uuid4()
    site = FakeSite(
        id=site_id,
        display_name="",
        domain="example.com",
        target_config={},
        target_config_draft={},
    )
    pages = [FakePage(title="Туры Сочи")]
    db = FakeDB(site, pages, [])

    profile = _run(build_draft_profile(
        db, site_id, competitor_caller=lambda **_: ({"competitor_brands": []}, {}),
    ))

    expected_keys = {
        "services", "excluded_services",
        "geo_primary", "geo_secondary", "excluded_geo",
        "competitor_brands",
        "months", "day_counts",
        "service_weights", "geo_weights",
    }
    assert expected_keys.issubset(profile.draft_config.keys())


def test_build_draft_profile_llm_accepts_returned_brands():
    site_id = uuid.uuid4()
    site = FakeSite(
        id=site_id,
        display_name="ЮК",
        domain="ukontinent.example",
        target_config={},
        target_config_draft={},
    )
    pages = [FakePage(title="Сочи")]
    queries = [
        FakeQuery(query_text="конкурент-сочи-экс"),
        FakeQuery(query_text="другой-конкурент"),
    ]
    db = FakeDB(site, pages, queries)

    def _good_caller(**_kwargs):
        return (
            {
                "competitor_brands": [
                    {"name": "Конкурент Х", "confidence_ru": 0.9},
                    {"name": "Конкурент Y", "confidence_ru": 0.7},
                ]
            },
            {"cost_usd": 0.002},
        )

    profile = _run(build_draft_profile(
        db, site_id, competitor_caller=_good_caller,
    ))
    names = profile.draft_config["competitor_brands"]
    assert "Конкурент Х" in names
    assert "Конкурент Y" in names
    assert profile.signals["competitor_brands_count"] == 2
