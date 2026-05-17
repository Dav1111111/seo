"""Deterministic per-category verifiers for advice cards.

Every verifier re-runs the SAME deterministic check that produced the
card in the first place — never an LLM. The point is to honestly say
«fact changed on the page» vs «owner pressed Применил but nothing
actually moved».

Anti-fabrication (CLAUDE.md rule 5): if a verifier can't decide cleanly,
it returns `not_yet_visible` (we ran but didn't see the change) — NEVER
silently `verified` to please the owner.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.advisor.verification.dispatcher import VerificationResult
from app.models.analysis_event import AnalysisEvent
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.search_query import SearchQuery
from app.models.site import Site

log = logging.getLogger(__name__)


# Max age of a deep extract we still consider «recent enough» to base a
# verification decision on. Past this, we treat the extract as stale —
# verifier returns `not_yet_visible` with «нужен свежий снимок» message.
_DEEP_EXTRACT_FRESH_HOURS = 24


# ── Helpers ─────────────────────────────────────────────────────────


async def _site(db: AsyncSession, site_id: UUID) -> Site | None:
    return await db.get(Site, site_id)


async def _latest_deep_extract_for_page(
    db: AsyncSession, page_id: UUID,
) -> PageDeepExtract | None:
    return (await db.execute(
        select(PageDeepExtract)
        .where(
            PageDeepExtract.page_id == page_id,
            PageDeepExtract.status == "completed",
            PageDeepExtract.is_competitor.is_(False),
        )
        .order_by(desc(PageDeepExtract.extracted_at))
        .limit(1)
    )).scalar_one_or_none()


def _is_fresh(extract: PageDeepExtract | None) -> bool:
    if extract is None or extract.extracted_at is None:
        return False
    age = datetime.now(timezone.utc) - extract.extracted_at
    return age <= timedelta(hours=_DEEP_EXTRACT_FRESH_HOURS)


def _normalize_schema_types(blocks: list[dict] | None) -> set[str]:
    types: set[str] = set()
    if not blocks:
        return types
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("@type")
        if isinstance(t, str):
            types.add(t.replace("http://schema.org/", "").replace("https://schema.org/", ""))
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.add(x.replace("http://schema.org/", "").replace("https://schema.org/", ""))
    return types


def _page_id_from_link(link: str | None) -> UUID | None:
    if not link:
        return None
    marker = "/studio/pages/"
    if marker not in link:
        return None
    raw = link.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
    try:
        return UUID(raw)
    except ValueError:
        return None


_SCHEMA_TYPE_CANONICAL: dict[str, str] = {
    "touristtrip": "TouristTrip", "offer": "Offer", "product": "Product",
    "faqpage": "FAQPage", "service": "Service",
    "aggregateoffer": "AggregateOffer", "breadcrumblist": "BreadcrumbList",
    "article": "Article", "howto": "HowTo", "organization": "Organization",
    "localbusiness": "LocalBusiness", "itemlist": "ItemList",
}


# ── 1. Schema ───────────────────────────────────────────────────────


async def verify_schema(
    db: AsyncSession, site_id: UUID, card_id: str, *, card_link: str | None,
) -> VerificationResult:
    """Schema fix: page now has the previously-missing @type."""
    # Parse card_id «schema:missing_type:faqpage» → «FAQPage»
    prefix = "schema:missing_type:"
    raw = card_id[len(prefix):] if card_id.startswith(prefix) else ""
    missing_type = _SCHEMA_TYPE_CANONICAL.get(raw.lower(), raw.title()) if raw else ""
    if not missing_type:
        return VerificationResult(
            status="failed",
            evidence={"reason": "card_id has no schema type"},
            message_ru="Не смог понять какой тип Schema проверять.",
        )

    page_id = _page_id_from_link(card_link)
    if page_id is None:
        return VerificationResult(
            status="user_attested",
            evidence={"reason": "no page in card link", "missing_type": missing_type},
            message_ru="Карточка не привязана к конкретной странице — принимаем на слово.",
        )

    extract = await _latest_deep_extract_for_page(db, page_id)
    if extract is None:
        return VerificationResult(
            status="not_yet_visible",
            evidence={"reason": "no deep extract for page", "missing_type": missing_type},
            message_ru="Браузерный снимок страницы не сделан — запусти пере-сбор и проверь снова.",
        )
    if not _is_fresh(extract):
        return VerificationResult(
            status="not_yet_visible",
            evidence={
                "reason": "stale deep extract",
                "missing_type": missing_type,
                "extracted_at": extract.extracted_at.isoformat(),
            },
            message_ru="Снимок страницы старше суток — для честной проверки нужен свежий.",
        )

    present_types = _normalize_schema_types(extract.schema_blocks)
    if missing_type in present_types:
        return VerificationResult(
            status="verified",
            evidence={
                "missing_type": missing_type,
                "present_types": sorted(present_types),
                "extracted_at": extract.extracted_at.isoformat(),
            },
            message_ru=f"На странице теперь есть разметка {missing_type} — факт подтверждён.",
        )

    return VerificationResult(
        status="not_yet_visible",
        evidence={
            "missing_type": missing_type,
            "present_types": sorted(present_types),
            "extracted_at": extract.extracted_at.isoformat(),
        },
        message_ru=(
            f"Разметка {missing_type} на странице пока не появилась — "
            "проверь что правка задеплоилась и попробуй ещё раз."
        ),
    )


# ── 2. Robots ───────────────────────────────────────────────────────


async def verify_robots(db: AsyncSession, site_id: UUID) -> VerificationResult:
    """Robots fix: re-run audit, check critical issues count went to 0."""
    site = await _site(db, site_id)
    if site is None:
        return VerificationResult(
            status="failed",
            evidence={"reason": "site not found"},
            message_ru="Сайт не найден.",
        )

    try:
        # Reuse the studio endpoint helper which writes the cache event.
        from app.api.v1.studio import _run_robots_audit_for_site
        audit = await _run_robots_audit_for_site(db, site)
    except Exception as exc:  # noqa: BLE001
        log.warning("advice.verify_robots_failed err=%s", exc)
        return VerificationResult(
            status="failed",
            evidence={"error": str(exc)[:300]},
            message_ru="Не удалось перепроверить robots.txt.",
        )

    issues = audit.get("issues") if isinstance(audit, dict) else []
    critical_codes = [
        i.get("code") for i in issues or []
        if isinstance(i, dict) and i.get("severity") == "critical"
    ]
    if not critical_codes:
        return VerificationResult(
            status="verified",
            evidence={"critical_codes_now": [], "all_issues_count": len(issues or [])},
            message_ru="Критических проблем в robots.txt больше нет — факт подтверждён.",
        )
    return VerificationResult(
        status="not_yet_visible",
        evidence={"critical_codes_now": critical_codes},
        message_ru=(
            f"В robots.txt всё ещё есть критика: {', '.join(critical_codes[:3])}. "
            "Проверь что файл задеплоился."
        ),
    )


# ── 3. Keywords ─────────────────────────────────────────────────────


async def verify_keywords(
    db: AsyncSession, site_id: UUID, card_id: str, *, card_link: str | None,
) -> VerificationResult:
    """Keyword placement: re-extract page, check missing tokens are now
    in title/H1. For the aggregate «keywords:gaps» card — re-run
    keyword_match on the whole site and compare total gaps count.
    """
    # Aggregate card — re-run the whole module.
    if card_id == "keywords:gaps":
        try:
            from app.core_audit.keyword_match.matcher import compute_keyword_gaps
            gaps = await compute_keyword_gaps(db, site_id)
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                status="failed",
                evidence={"error": str(exc)[:300]},
                message_ru="Не удалось перепроверить ключевые слова.",
            )
        if not gaps:
            return VerificationResult(
                status="verified",
                evidence={"after_gap_count": 0},
                message_ru="Дыр по ключевым словам больше нет — факт подтверждён.",
            )
        return VerificationResult(
            status="not_yet_visible",
            evidence={"after_gap_count": len(gaps)},
            message_ru=f"После правки дыр всё ещё {len(gaps)} — проверь страницы и попробуй снова.",
        )

    # Per-page keyword card («keyword_placement.{query_id}»).
    # Strategy: re-extract the page, check that the title/H1 now contains
    # at least one previously-missing lemma. If we don't know which page
    # the card was about → user_attested.
    page_id = _page_id_from_link(card_link)
    if page_id is None:
        return VerificationResult(
            status="user_attested",
            evidence={"reason": "no page in card link"},
            message_ru="Карточка не привязана к странице — принимаем на слово.",
        )

    extract = await _latest_deep_extract_for_page(db, page_id)
    if extract is None or not _is_fresh(extract):
        return VerificationResult(
            status="not_yet_visible",
            evidence={
                "reason": "no fresh deep extract",
                "extracted_at": extract.extracted_at.isoformat() if extract else None,
            },
            message_ru="Нужен свежий снимок страницы — запусти пере-сбор и проверь снова.",
        )

    # Re-run keyword_match — if THIS page no longer appears in gaps for
    # the same query, the fix landed.
    try:
        from app.core_audit.keyword_match.matcher import compute_keyword_gaps
        gaps = await compute_keyword_gaps(db, site_id)
    except Exception as exc:  # noqa: BLE001
        return VerificationResult(
            status="failed",
            evidence={"error": str(exc)[:300]},
            message_ru="Не удалось пересчитать дыры по ключевым словам.",
        )
    # Parse query_id from card_id: «keyword_placement.{query_id}»
    query_id_raw = card_id.split(".", 1)[1] if "." in card_id else ""
    page_still_has_gap = any(
        getattr(g, "page_id", None) == page_id
        and (not query_id_raw or str(getattr(g, "query_id", "")) == query_id_raw)
        for g in gaps
    )
    if not page_still_has_gap:
        return VerificationResult(
            status="verified",
            evidence={"page_id": str(page_id), "query_id": query_id_raw},
            message_ru="Недостающие слова теперь в title/H1 — факт подтверждён.",
        )
    return VerificationResult(
        status="not_yet_visible",
        evidence={"page_id": str(page_id), "query_id": query_id_raw},
        message_ru="После правки слова всё ещё не вижу в title/H1 — проверь deploy.",
    )


# ── 4. Technical (stage failed) ─────────────────────────────────────


async def verify_technical(
    db: AsyncSession, site_id: UUID, card_id: str,
) -> VerificationResult:
    """Card «health:stage_failed:<stage>» — check that the latest event
    for that stage in the last 6h is `done`/`skipped`.
    """
    prefix = "health:stage_failed:"
    stage = card_id[len(prefix):] if card_id.startswith(prefix) else ""
    if not stage:
        return VerificationResult(
            status="failed", evidence={"reason": "no stage in card_id"},
            message_ru="Не смог понять стадию для проверки.",
        )
    since = datetime.now(timezone.utc) - timedelta(hours=6)
    latest = (await db.execute(
        select(AnalysisEvent.status, AnalysisEvent.ts, AnalysisEvent.message)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == stage,
            AnalysisEvent.ts >= since,
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1)
    )).first()
    if latest is None:
        return VerificationResult(
            status="not_yet_visible",
            evidence={"stage": stage, "since": since.isoformat()},
            message_ru=f"За последние 6 часов стадия {stage} не запускалась — попробуй её перезапустить.",
        )
    status, ts, message = latest
    if status in ("done", "skipped"):
        return VerificationResult(
            status="verified",
            evidence={"stage": stage, "latest_status": status, "latest_ts": ts.isoformat()},
            message_ru=f"Последний запуск {stage} прошёл успешно — факт подтверждён.",
        )
    return VerificationResult(
        status="not_yet_visible",
        evidence={"stage": stage, "latest_status": status, "latest_message": message},
        message_ru=f"Стадия {stage} всё ещё падает: {(message or '')[:120]}",
    )


# ── 5. Metrica counter health ───────────────────────────────────────


async def verify_health_metrica(db: AsyncSession, site_id: UUID) -> VerificationResult:
    """Re-check Metrica counter_code_status. CS_OK → verified, else
    not_yet_visible.
    """
    site = await _site(db, site_id)
    if site is None:
        return VerificationResult(
            status="failed", evidence={"reason": "site not found"},
            message_ru="Сайт не найден.",
        )
    counter_id = getattr(site, "yandex_metrica_counter_id", None)
    if not counter_id:
        return VerificationResult(
            status="user_attested",
            evidence={"reason": "no counter_id on site"},
            message_ru="ID счётчика Метрики не задан — принимаем на слово.",
        )
    try:
        from app.collectors.metrica import MetricaCollector
        from app.config import settings
        token = settings.YANDEX_METRICA_OAUTH_TOKEN
        if not token:
            return VerificationResult(
                status="user_attested",
                evidence={"reason": "no metrica oauth token in env"},
                message_ru="Нет OAuth-токена Метрики в .env — принимаем на слово.",
            )
        collector = MetricaCollector(token, counter_id)
        try:
            info = await collector.fetch_counter_info()
        finally:
            await collector.close()
    except Exception as exc:  # noqa: BLE001
        return VerificationResult(
            status="failed",
            evidence={"error": str(exc)[:300]},
            message_ru="Не удалось перепроверить статус счётчика Метрики.",
        )
    code_status = ""
    if isinstance(info, dict):
        counter = info.get("counter") if isinstance(info.get("counter"), dict) else {}
        code_status = (counter or {}).get("code_status", "") or ""
    if code_status == "CS_OK":
        return VerificationResult(
            status="verified",
            evidence={"code_status": "CS_OK"},
            message_ru="Счётчик Метрики теперь отвечает корректно — факт подтверждён.",
        )
    return VerificationResult(
        status="not_yet_visible",
        evidence={"code_status": code_status or "unknown"},
        message_ru=(
            f"Метрика всё ещё отвечает «{code_status or 'непонятно'}». "
            "Проверь установку кода счётчика на сайте."
        ),
    )


# ── 6. Funnel top — variant B (crawler discovery) ───────────────────


_DISCOVERY_TOKENS: tuple[str, ...] = (
    "развлеч", "посмотр", "сходить", "досуг", "достопримеч",
    "интересн", "необычн", "чем заняться", "что делать",
)


async def verify_funnel_top(
    db: AsyncSession, site_id: UUID, card_id: str,
) -> VerificationResult:
    """Variant B: scan `pages` table for a page that looks like a
    funnel-top landing — title/h1 contains discovery tokens AND was
    created/updated in the last 14 days. The beat task re-runs daily
    so the owner has time to publish.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    rows = (await db.execute(
        select(Page.id, Page.url, Page.title, Page.h1)
        .where(
            Page.site_id == site_id,
            Page.created_at >= cutoff,
        )
        .limit(100)
    )).all()
    candidates: list[dict[str, Any]] = []
    for page_id, url, title, h1 in rows:
        haystack = ((title or "") + " " + (h1 or "")).lower()
        matches = [t for t in _DISCOVERY_TOKENS if t in haystack]
        if matches:
            candidates.append({
                "page_id": str(page_id),
                "page_url": url,
                "matched_tokens": matches,
            })
    if candidates:
        first = candidates[0]
        return VerificationResult(
            status="verified",
            evidence={
                "candidate_page_id": first["page_id"],
                "candidate_page_url": first["page_url"],
                "matched_tokens": first["matched_tokens"],
                "total_candidates": len(candidates),
            },
            message_ru=(
                f"Нашёл новую страницу под верх воронки: {first['page_url']}. "
                "Факт подтверждён."
            ),
        )
    return VerificationResult(
        status="not_yet_visible",
        evidence={"checked_pages": len(rows), "cutoff": cutoff.isoformat()},
        message_ru=(
            "Новой посадочной под верх воронки пока не вижу. "
            "Когда опубликуешь страницу — система найдёт её при следующем обходе."
        ),
    )


# ── 7. SEO content / brain catch-all ────────────────────────────────


async def verify_seo_content(
    db: AsyncSession, site_id: UUID, card_id: str, *, card_category: str,
) -> VerificationResult:
    """Generic brain-rule verifier. For aggregate counts we re-run the
    same SQL the rule used and compare. For things we can't auto-check
    (e.g. «66 рекомендаций ждут твоего решения» — needs owner to apply
    them one by one), we return `user_attested`.
    """
    # «brain:queries:harmful» — count harmful queries went down.
    if card_id == "brain:queries:harmful":
        cnt = (await db.execute(
            select(func.count())
            .select_from(SearchQuery)
            .where(
                SearchQuery.site_id == site_id,
                SearchQuery.relevance == "spam",
            )
        )).scalar_one()
        if cnt == 0:
            return VerificationResult(
                status="verified",
                evidence={"harmful_count": 0},
                message_ru="Вредных запросов больше нет — факт подтверждён.",
            )
        return VerificationResult(
            status="not_yet_visible",
            evidence={"harmful_count": int(cnt)},
            message_ru=f"Вредных запросов всё ещё {cnt} — нужно вручную пометить как spam.",
        )

    # Anything else from the brain catalog — owner-driven content work.
    return VerificationResult(
        status="user_attested",
        evidence={"reason": "brain card needs owner-side content work"},
        message_ru=(
            "Эта карточка про контентную правку — автоматически проверить "
            "нельзя, принимаем на слово."
        ),
    )


__all__ = [
    "verify_schema",
    "verify_robots",
    "verify_keywords",
    "verify_technical",
    "verify_health_metrica",
    "verify_funnel_top",
    "verify_seo_content",
]
