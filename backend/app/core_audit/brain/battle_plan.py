"""Deterministic battle-plan renderer for /studio/chat.

This is deliberately not an LLM prompt. The owner wants a strong SEO
plan, but the plan must stay grounded: URL, query, competitor and
expected effect all come from BrainSnapshot / BrainPlan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core_audit.brain.rules import Plan
from app.core_audit.brain.snapshot import BrainSnapshot


@dataclass
class BattlePlanItem:
    id: str
    source: str
    title_ru: str
    object_ru: str
    reason_ru: str
    action_ru: str
    expected_effect_ru: str
    verify_ru: str
    link_to: str
    score: float
    detail_ru: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


_PRIORITY_SCORE = {
    "critical": 95.0,
    "high": 82.0,
    "medium": 62.0,
    "low": 35.0,
}


# Evidence keys that are LLM/system-internal only and must NEVER leak
# into owner-facing markdown. `source_finding_id` is the Python-check
# identifier (e.g. `commercial.missing_phone_in_header`); the owner
# should see the human reason, not the code path.
_INTERNAL_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "source_finding_id",
})


def _evidence_for_owner(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strip internal-only keys before rendering for the owner.

    The full evidence dict still travels with the BattlePlanItem so
    LLM context builders and downstream consumers can use it; only the
    markdown rendering goes through this filter.
    """
    if not evidence:
        return {}
    return {k: v for k, v in evidence.items() if k not in _INTERNAL_EVIDENCE_KEYS}


def build_battle_plan_items(
    snap: BrainSnapshot,
    plan: Plan,
    *,
    limit: int = 5,
) -> list[BattlePlanItem]:
    """Return the strongest grounded actions, capped for execution.

    Ranking is deterministic. It prefers confirmed technical blockers,
    then page-level pending recommendations, then competitor gaps,
    harmful visibility, missing landing pages and follow-up checks.
    """
    items: list[BattlePlanItem] = []
    items.extend(_indexation_items(snap))
    items.extend(_page_recommendation_items(snap))
    items.extend(_competitor_items(snap))
    items.extend(_query_items(snap))
    items.extend(_missing_landing_items(snap))
    items.extend(_outcome_items(snap))
    items.extend(_plan_fallback_items(plan))

    deduped: list[BattlePlanItem] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda it: (-it.score, it.source, it.id)):
        key = item.id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def render_battle_plan_reply(snap: BrainSnapshot, plan: Plan) -> str:
    items = build_battle_plan_items(snap, plan, limit=5)
    facts = _facts(snap)
    missing = _missing_data(snap, plan)

    lines = [
        "## Боевой SEO-план",
        "",
        "Это план на рост к топ-5, но не гарантия топ-5: позиции зависят от Яндекса, конкурентов и скорости применения правок.",
        "",
        "### Факты, на которых строю план",
    ]
    lines.extend([f"- {fact}" for fact in facts] or ["- В данных пока мало фактов: сначала надо запустить сборы и ревью."])

    lines.extend(["", "### План действий"])
    if not items:
        lines.append("Сейчас нет достаточно сильных действий в данных. Сначала добери данные из блока ниже.")
    for idx, item in enumerate(items, start=1):
        lines.extend([
            f"{idx}. **{item.title_ru}**",
            f"   - Где: {item.object_ru} ({item.source})",
            f"   - Почему: {item.reason_ru}",
            *([f"   - Детали: {item.detail_ru}"] if item.detail_ru else []),
            f"   - Что сделать: {item.action_ru}",
            f"   - Ожидаемый эффект: {item.expected_effect_ru}",
            f"   - Проверка: {item.verify_ru}",
            f"   - Куда открыть: {item.link_to}",
        ])
        evidence = _format_evidence(_evidence_for_owner(item.evidence))
        if evidence:
            lines.append(f"   - Основание: {evidence}")

    lines.extend([
        "",
        "### Проверка результата",
        "- После правок на страницах отмечай рекомендацию как «применил»: система фиксирует baseline и через 14 дней покажет site-wide дельту в /studio/outcomes.",
        "- Для индексации проверяй /studio/indexation и Webmaster: unknown не считай ошибкой, пока нет подтверждённого статуса.",
        "- Для конкурентов повторяй SERP/deep-dive после крупных изменений или если данные помечены как устаревшие.",
        "",
        "### Что добрать",
    ])
    lines.extend([f"- {row}" for row in missing] or ["- Критичных пробелов в данных для этого плана сейчас не вижу."])
    return "\n".join(lines)


def battle_plan_result(snap: BrainSnapshot, plan: Plan) -> dict[str, Any]:
    return {
        "reply": render_battle_plan_reply(snap, plan),
        "proposal": None,
        "cost_usd": 0.0,
        "model": "rules:battle-plan",
        "input_tokens": 0,
        "output_tokens": 0,
        "truncated": False,
        "stop_reason": "deterministic",
    }


def _indexation_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    idx = snap.indexation
    items: list[BattlePlanItem] = []
    if idx.non_200_count > 0:
        sample = idx.sample_non_200[0] if idx.sample_non_200 else {}
        items.append(BattlePlanItem(
            id="indexation:non_200",
            source="/studio/indexation",
            title_ru="Закрыть страницы с HTTP-ошибками",
            object_ru=str(sample.get("url") or f"{idx.non_200_count} non-200 URL"),
            reason_ru=f"Найдено {idx.non_200_count} страниц с non-200. Поисковик не сможет нормально оценить такую страницу.",
            action_ru="Исправь статус на 200 или поставь корректный 301 на рабочую страницу.",
            expected_effect_ru="Снимает технический блокер для обхода и индексации.",
            verify_ru="После исправления перезапусти crawl и проверь, что URL вернулся с HTTP 200.",
            link_to="/studio/indexation",
            score=100.0,
            detail_ru=_indexation_detail(sample),
            evidence={"non_200_count": idx.non_200_count, **sample},
        ))
    if idx.noindex_count > 0:
        url = (idx.sample_noindex or [""])[0]
        items.append(BattlePlanItem(
            id="indexation:noindex",
            source="/studio/indexation",
            title_ru="Проверить accidental noindex",
            object_ru=url or f"{idx.noindex_count} noindex URL",
            reason_ru=f"Найдено {idx.noindex_count} страниц с noindex. Если это важные посадочные, Яндекс не должен их индексировать.",
            action_ru="Убери noindex с важных страниц или явно оставь его только на служебных.",
            expected_effect_ru="Возвращает важные страницы в кандидаты на индексацию.",
            verify_ru="Пересканируй страницу и затем проверь per-URL статус в Webmaster.",
            link_to="/studio/indexation",
            score=96.0,
            detail_ru=(
                f"Пример из данных: {url}. Если это служебная страница, "
                "noindex может быть нормальным; если посадочная — это блокер."
                if url else ""
            ),
            evidence={"noindex_count": idx.noindex_count, "url": url},
        ))
    if idx.sample_not_indexed_urls:
        url = idx.sample_not_indexed_urls[0]
        items.append(BattlePlanItem(
            id="indexation:not_indexed",
            source="/studio/indexation",
            title_ru="Разобрать подтверждённо неиндексируемые URL",
            object_ru=url,
            reason_ru="В контексте есть URL, который подтверждённо не в индексе Webmaster. Это не то же самое, что unknown.",
            action_ru="Проверь для него HTTP, canonical, sitemap, noindex и качество текста; после исправления отправь на переобход.",
            expected_effect_ru="Убирает причину выпадения страницы из поиска, если она техническая или контентная.",
            verify_ru="Проверь URL в /studio/indexation и Webmaster после переобхода.",
            link_to="/studio/indexation",
            score=90.0,
            detail_ru=(
                "Это подтверждённый статус Webmaster. Unknown-страницы не "
                "смешивай с этим списком: у них просто не хватает проверки."
            ),
            evidence={"url": url},
        ))
    canonical_count = (
        idx.canonical_external_count
        + idx.canonical_mismatch_count
        + idx.canonical_missing_count
    )
    if canonical_count > 0:
        sample = idx.sample_canonical_issues[0] if idx.sample_canonical_issues else {}
        items.append(BattlePlanItem(
            id="indexation:canonical",
            source="/studio/indexation",
            title_ru="Навести порядок с canonical",
            object_ru=str(sample.get("url") or f"{canonical_count} canonical issues"),
            reason_ru=(
                "Canonical помогает Яндексу понять главную версию страницы. "
                f"Сейчас найдено {canonical_count} проблемных canonical-сигналов."
            ),
            action_ru="Поставь self-canonical на важные страницы и убери canonical на чужой/неверный URL.",
            expected_effect_ru="Снижает риск, что Яндекс выберет не ту страницу для ранжирования.",
            verify_ru="Пересканируй страницу и проверь, что canonical совпадает с целевым URL.",
            link_to="/studio/indexation",
            score=84.0,
            detail_ru=_indexation_detail(sample),
            evidence={
                "canonical_missing": idx.canonical_missing_count,
                "canonical_external": idx.canonical_external_count,
                "canonical_mismatch": idx.canonical_mismatch_count,
                **sample,
            },
        ))
    return items


def _page_recommendation_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    out: list[BattlePlanItem] = []
    for rec in (snap.review.top_pending_recommendations or [])[:12]:
        priority = str(rec.get("priority") or "medium").lower()
        base = _PRIORITY_SCORE.get(priority, 50.0)
        score = rec.get("priority_score")
        numeric_score = float(score) if score is not None else base
        title = f"Применить {priority}-рекомендацию по странице"
        url = str(rec.get("url") or "/studio/pages")
        action = str(rec.get("after_text") or "").strip()
        if not action:
            action = "Открой страницу в /studio/pages и примени конкретную рекомендацию из ревью."
        detail = _page_recommendation_detail(rec)
        out.append(BattlePlanItem(
            id=f"review:{rec.get('rec_id') or url}:{rec.get('category')}",
            source="/studio/pages",
            title_ru=title,
            object_ru=url,
            reason_ru=str(rec.get("reasoning_ru") or "Рекомендация пришла из последнего completed review страницы."),
            action_ru=action,
            expected_effect_ru="Улучшает релевантность посадочной страницы под её интент; эффект проверяется по данным после применения.",
            verify_ru="Отметь рекомендацию как «применил», затем через 14 дней смотри /studio/outcomes и позиции/показы в Webmaster.",
            link_to="/studio/pages",
            score=max(base, numeric_score),
            detail_ru=detail,
            evidence={
                "rec_id": rec.get("rec_id"),
                "priority": rec.get("priority"),
                "category": rec.get("category"),
                "priority_score": rec.get("priority_score"),
                "impact_score": rec.get("impact_score"),
                "confidence_score": rec.get("confidence_score"),
                "ease_score": rec.get("ease_score"),
                "source_finding_id": rec.get("source_finding_id"),
                "target_intent_code": rec.get("target_intent_code"),
                "url": url,
            },
        ))
    return out


def _competitor_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    out: list[BattlePlanItem] = []
    comp = snap.competitors
    for idx, opp in enumerate((comp.growth_opportunities or [])[:6], start=1):
        priority = str(opp.get("priority") or "medium").lower()
        evidence = opp.get("evidence") if isinstance(opp.get("evidence"), dict) else {}
        domain = evidence.get("competitor_domain") or (
            (evidence.get("competitors_with") or [""])[0]
            if isinstance(evidence.get("competitors_with"), list)
            else ""
        )
        out.append(BattlePlanItem(
            id=f"competitors:{opp.get('id') or idx}",
            source="/studio/competitors",
            title_ru=str(opp.get("title_ru") or "Закрыть конкурентный разрыв"),
            object_ru=str(domain or opp.get("category") or "competitor opportunity"),
            reason_ru=str(opp.get("reasoning_ru") or "Эта возможность посчитана модулем конкурентов из SERP/deep-dive данных."),
            action_ru=str(opp.get("suggested_action_ru") or "Открой /studio/competitors и закрой найденный gap."),
            expected_effect_ru="Закрывает отличие, которое система увидела у конкурентов; это повышает шанс конкурировать по тем же интентам.",
            verify_ru="После правки повтори competitor deep-dive и сравни, исчез ли gap.",
            link_to="/studio/competitors",
            score=_PRIORITY_SCORE.get(priority, 60.0) - (15.0 if comp.profile_is_stale else 0.0),
            detail_ru=_competitor_detail(opp, evidence),
            evidence=evidence | {
                "priority": priority,
                "source": opp.get("source"),
                "profile_stale_days": comp.profile_stale_days,
            },
        ))
    return out


def _query_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    q = snap.queries
    if q.spam + q.disputed <= 0:
        return []
    examples = q.sample_harmful or []
    example = examples[0] if examples else {}
    query = str(example.get("query_text") or "вредные/спорные запросы")
    return [BattlePlanItem(
        id="queries:harmful",
        source="/studio/queries/harmful",
        title_ru="Почистить вредную видимость",
        object_ru=query,
        reason_ru=f"В запросах есть {q.spam} spam и {q.disputed} disputed. Такие сигналы мешают понять, за что сайт должен ранжироваться.",
        action_ru="Открой вредную видимость, проверь примеры и поправь классификацию или контентные сигналы.",
        expected_effect_ru="Уточняет тематический профиль сайта и снижает шум в SEO-решениях.",
        verify_ru="После правок повтори классификацию запросов и проверь, уменьшились ли spam/disputed.",
        link_to="/studio/queries/harmful",
        score=76.0,
        detail_ru=(
            f"Пример из данных: «{query}»"
            + (f" — {example.get('reason_ru')}" if example.get("reason_ru") else "")
            if example else ""
        ),
        evidence={
            "spam": q.spam,
            "disputed": q.disputed,
            "example_query": query,
            "reason_ru": example.get("reason_ru"),
        },
    )]


def _missing_landing_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    m = snap.missing_landings
    if m.total <= 0:
        return []
    item = m.items[0] if m.items else {}
    service = str(item.get("service_name") or "услуга без отдельной страницы")
    priority = str(item.get("priority") or "medium").lower()
    return [BattlePlanItem(
        id="missing_landings:create",
        source="/studio/competitors",
        title_ru="Создать посадочную под услугу без страницы",
        object_ru=service,
        reason_ru=f"Найдено {m.total} услуг без отдельной страницы; важных: {m.high_priority}.",
        action_ru=f"Собери отдельную посадочную под «{service}» с title, H1, оффером, ценами/условиями и CTA.",
        expected_effect_ru="Даёт отдельную релевантную страницу под спрос вместо попытки ранжировать общий URL.",
        verify_ru="После публикации добавь URL в sitemap, отправь на переобход и затем проверь показы/позиции в Webmaster.",
        link_to="/studio/competitors",
        score=_PRIORITY_SCORE.get(priority, 64.0),
        detail_ru=(
            f"Приоритет: {priority}. "
            + (
                f"Цитата-основание: «{item.get('evidence_quote')}»."
                if item.get("evidence_quote") else
                "Основание хранится в списке missing landings."
            )
        ),
        evidence={
            "total": m.total,
            "high_priority": m.high_priority,
            "service_name": service,
            "stale_days": m.stale_days,
        },
    )]


def _outcome_items(snap: BrainSnapshot) -> list[BattlePlanItem]:
    o = snap.outcomes
    if o.pending_followup <= 0:
        return []
    return [BattlePlanItem(
        id="outcomes:followup",
        source="/studio/outcomes",
        title_ru="Дождаться и проверить эффект применённых правок",
        object_ru=f"{o.pending_followup} pending follow-up",
        reason_ru="Есть применённые правки, по которым ещё не пришёл follow-up замер.",
        action_ru="Пока не меняй всё подряд на тех же страницах; дождись контрольного замера, чтобы не смешать эффекты.",
        expected_effect_ru="Даёт понимание, какие типы правок реально сработали на сайте.",
        verify_ru="Через 14 дней после отметки «применил» проверь /studio/outcomes. Сейчас замер site-wide, не page-level.",
        link_to="/studio/outcomes",
        score=30.0,
        detail_ru=(
            "Здесь важно не накладывать несколько крупных правок на один "
            "и тот же участок до контрольного замера, иначе будет непонятно, "
            "что именно дало эффект."
        ),
        evidence={"pending_followup": o.pending_followup, "applied_total": o.applied_total},
    )]


def _plan_fallback_items(plan: Plan) -> list[BattlePlanItem]:
    out: list[BattlePlanItem] = []
    for action in plan.actions:
        out.append(BattlePlanItem(
            id=f"plan:{action.id}",
            source=action.link_to,
            title_ru=action.title,
            object_ru=action.link_label,
            reason_ru=action.body_ru,
            action_ru=action.what_to_do_ru,
            expected_effect_ru="Закрывает уже посчитанное системой действие из текущего плана.",
            verify_ru="Вернись в тот же модуль и проверь, ушло ли действие из текущего плана.",
            link_to=action.link_to,
            score=_PRIORITY_SCORE.get(action.severity, 40.0) - 5.0,
            detail_ru="Это fallback из текущего BrainPlan, если более конкретных page/competitor действий не хватило.",
            evidence=action.evidence,
        ))
    return out


def _facts(snap: BrainSnapshot) -> list[str]:
    idx = snap.indexation
    q = snap.queries
    r = snap.review
    c = snap.competitors
    facts = [
        f"Страниц в системе: {idx.pages_total}; в индексе Webmaster: {idx.pages_in_index}; excluded: {idx.pages_excluded}; unknown: {idx.pages_unknown}.",
        f"Проверенный per-URL статус Webmaster есть у {idx.checked_pages} страниц; unknown — это нехватка проверки, а не автоматическая ошибка.",
        f"Ожидающих рекомендаций по страницам: {r.recs_pending}; high/critical: {r.recs_high_priority_pending}.",
    ]
    if idx.last_checked_at:
        facts.append(f"Последняя per-URL проверка Webmaster: {_short_dt(idx.last_checked_at)}.")
    if q.total > 0:
        facts.append(
            f"Запросов: {q.total}; own: {q.own}; spam/disputed: "
            f"{q.spam + q.disputed}; unclassified: {q.unclassified}."
        )
    if c.profile_available:
        facts.append(f"SERP-конкуренты разведаны: запросов {c.queries_probed}, внешних доменов {c.unique_domains_seen}.")
    if c.growth_opportunities:
        facts.append(f"Возможностей роста из анализа конкурентов: {len(c.growth_opportunities)}.")
    if snap.missing_landings.total > 0:
        facts.append(f"Услуг без отдельной страницы: {snap.missing_landings.total}; важных: {snap.missing_landings.high_priority}.")
    return facts[:7]


def _missing_data(snap: BrainSnapshot, plan: Plan) -> list[str]:
    out = list(plan.diagnostics or [])
    if not snap.competitors.profile_available:
        out.append("Нет SERP-разведки конкурентов: запусти /studio/competitors, иначе конкурентный блок плана будет неполным.")
    elif snap.competitors.profile_is_stale:
        out.append(f"SERP-разведка конкурентов устарела на {snap.competitors.profile_stale_days} дней: перед крупными решениями лучше обновить.")
    if not snap.competitors.deep_dive_available:
        out.append("Нет deep-dive по SEO-признакам конкурентов: нельзя честно сравнить цены, CTA, отзывы, schema и структуру страниц.")
    if snap.indexation.pages_unknown > 0:
        out.append(f"У {snap.indexation.pages_unknown} страниц статус индексации unknown: это не ошибка, но нужен per-URL Webmaster check.")
    # Wordstat-coverage warning fires whenever fewer than half the
    # classified queries have an answer from Wordstat (audit-2026-05-15).
    # Mirrors `_rule_wordstat_partial_coverage` in rules.py so the
    # battle plan and the action plan stay in sync.
    if snap.queries.total > 0:
        coverage = snap.queries.with_volume_known / snap.queries.total
        if coverage < 0.5:
            out.append(
                f"Wordstat-объёмы собраны только для "
                f"{snap.queries.with_volume_known} из {snap.queries.total} "
                f"запросов: без этого нельзя честно ранжировать по спросу."
            )
    return _unique(out)[:6]


def _format_evidence(evidence: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in evidence.items():
        if value is None or value == "":
            continue
        text = str(value)
        if len(text) > 160:
            text = text[:157] + "..."
        parts.append(f"{key}={text}")
        if len(parts) >= 7:
            break
    return ", ".join(parts)


def _indexation_detail(sample: dict[str, Any]) -> str:
    if not sample:
        return ""
    bits: list[str] = []
    if sample.get("url"):
        bits.append(f"пример URL: {sample.get('url')}")
    if sample.get("http_status"):
        bits.append(f"HTTP {sample.get('http_status')}")
    if sample.get("kind"):
        bits.append(f"тип проблемы: {sample.get('kind')}")
    if sample.get("canonical"):
        bits.append(f"canonical: {sample.get('canonical')}")
    return "; ".join(bits)


def _page_recommendation_detail(rec: dict[str, Any]) -> str:
    bits: list[str] = []
    if rec.get("rec_id"):
        bits.append(f"rec_id={rec.get('rec_id')}")
    if rec.get("category"):
        bits.append(f"категория={rec.get('category')}")
    if rec.get("target_intent_code"):
        bits.append(f"интент={rec.get('target_intent_code')}")
    # `source_finding_id` is an internal Python-check identifier
    # (e.g. `commercial.missing_phone_in_header`). It's useful as LLM
    # context inside `evidence` but must NOT leak into owner-facing
    # markdown — see `_evidence_for_owner`.
    if rec.get("priority_score") is not None:
        bits.append(f"score={rec.get('priority_score')}")
    scoring = []
    for key, label in (
        ("impact_score", "impact"),
        ("confidence_score", "confidence"),
        ("ease_score", "ease"),
    ):
        if rec.get(key) is not None:
            scoring.append(f"{label}={rec.get(key)}")
    if scoring:
        bits.append("оценки: " + ", ".join(scoring))
    before = str(rec.get("before_text") or "").strip()
    if before:
        bits.append(f"сейчас в данных: «{before}»")
    after = str(rec.get("after_text") or "").strip()
    if after:
        bits.append(f"целевая правка: «{after}»")
    return "; ".join(bits)


def _competitor_detail(opp: dict[str, Any], evidence: dict[str, Any]) -> str:
    bits: list[str] = []
    if opp.get("source"):
        bits.append(f"источник={opp.get('source')}")
    if opp.get("category"):
        bits.append(f"категория={opp.get('category')}")
    if evidence.get("competitor_domain"):
        bits.append(f"конкурент={evidence.get('competitor_domain')}")
    competitors_with = evidence.get("competitors_with")
    if isinstance(competitors_with, list) and competitors_with:
        bits.append("есть у: " + ", ".join(map(str, competitors_with[:5])))
    queries = evidence.get("queries")
    if isinstance(queries, list) and queries:
        bits.append("запросы: " + ", ".join(f"«{q}»" for q in queries[:3]))
    if evidence.get("competitor_url"):
        bits.append(f"URL конкурента: {evidence.get('competitor_url')}")
    return "; ".join(bits)


def _short_dt(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = [
    "BattlePlanItem",
    "battle_plan_result",
    "build_battle_plan_items",
    "render_battle_plan_reply",
]
