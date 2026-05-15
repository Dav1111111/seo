"""Source-specific helpers that turn raw module signals into AdviceCard.

Every source the aggregator consumes has one formatter here. Keeping
them pure — input data only, no DB calls — so each is unit-testable
and the aggregator stays a thin pull-and-compose loop.

Rule of thumb: a formatter either returns an AdviceCard, or None when
the signal is too weak to surface (e.g. zero items, healthy state).
"""

from __future__ import annotations

from typing import Any

from app.core_audit.advisor.dto import (
    AdviceCard,
    Category,
    Severity,
    compute_sort_score,
)


# ── 1. Health / pipeline failures ─────────────────────────────────────


# Owner-friendly Russian labels for the pipeline stages we surface.
# Keep this list small — only stages whose persistent failure should
# block the owner's day. Anything not in the map falls back to a
# generic «сборщик X» phrasing in `_pretty_stage`.
_STAGE_LABEL_RU: dict[str, str] = {
    "crawl": "обход сайта",
    "webmaster": "Webmaster: данные по запросам",
    "demand_map": "Wordstat: карта спроса",
    "competitor_discovery": "Поиск конкурентов в SERP",
    "competitor_deep_dive": "Глубокий разбор конкурентов",
    "opportunities": "Подсветка возможностей",
    "priorities": "Приоритеты",
    "report": "Отчёт",
    "outcome": "Замер «до/после»",
    "robots_audit": "Аудит robots.txt",
    "keyword_gaps": "Сравнение запросов с текстом страниц",
    "wordstat_refresh_site": "Обновление объёмов Wordstat",
}


def _pretty_stage(stage: str) -> str:
    return _STAGE_LABEL_RU.get(stage, f"сборщик «{stage}»")


def format_health_failure(
    stage: str,
    count: int,
    last_message: str | None,
) -> AdviceCard:
    """Stage X failed `count` times in the last 24h → critical
    technical card. We don't try to diagnose — we surface that something
    is broken and route the owner to the activity log.

    `count` is honest: «1 раз» / «3 раза подряд». Severity is critical
    if 3+ retries, high otherwise (intermittent network blip).
    """
    severity: Severity = "critical" if count >= 3 else "high"
    label = _pretty_stage(stage)
    times = "раз" if count == 1 else ("раза" if 2 <= count <= 4 else "раз")
    title = f"Сборщик «{label}» падает ({count} {times} за 24 часа)"
    body_parts = [
        f"Подсистема «{label}» не отработала за последние сутки — "
        f"данные с этого источника устаревают, и зависящие от них "
        f"советы могут перестать быть актуальными.",
    ]
    if last_message:
        body_parts.append(f"Последняя ошибка: «{last_message[:200]}».")
    body = " ".join(body_parts)
    return AdviceCard(
        id=f"health:stage_failed:{stage}",
        severity=severity,
        category="technical",
        title_ru=title,
        body_ru=body,
        action_ru=(
            "Открой Активность и посмотри последний failed-запуск этой "
            "стадии — там видно полный stacktrace и какая внешняя "
            "система (Webmaster / Wordstat / SERP) вернула ошибку."
        ),
        expected_impact_ru=None,
        link="/studio/activity",
        cta_ru="Открыть активность",
        sort_score=compute_sort_score(severity, "technical"),
        source_module="advisor.health",
    )


# ── 2. Robots audit ───────────────────────────────────────────────────


def format_robots_critical(
    critical_issues: int,
    valid_for_yandex: bool,
) -> AdviceCard | None:
    """Critical issues in robots.txt → top-priority technical card.

    Mirrors the brain rule's wording — but we surface as `technical`
    here because robots.txt being broken is upstream of every SEO
    signal. Returns None when zero issues (healthy or never-ran).
    """
    if critical_issues <= 0:
        return None
    word = "проблема" if critical_issues == 1 else (
        "проблемы" if 2 <= critical_issues <= 4 else "проблем"
    )
    title = f"В robots.txt — {critical_issues} критических {word} для Яндекса"
    if valid_for_yandex:
        body = (
            f"Аудит robots.txt нашёл {critical_issues} критических {word}: "
            f"YandexBot может неправильно понять, какие страницы можно "
            f"индексировать. Это самый верх воронки — пока не починим, "
            f"остальные рекомендации могут работать впустую."
        )
    else:
        body = (
            f"robots.txt сейчас недоступен или не парсится для Яндекса, "
            f"плюс аудит уже нашёл {critical_issues} {word}. Мы не можем "
            f"гарантировать, по каким правилам YandexBot обходит сайт."
        )
    return AdviceCard(
        id="robots:critical",
        severity="critical",
        category="technical",
        title_ru=title,
        body_ru=body,
        action_ru=(
            "Открой «Индексация» → блок проверки robots.txt. Там по "
            "каждой проблеме видно цитату из файла и конкретную правку."
        ),
        expected_impact_ru=None,
        link="/studio/indexation",
        cta_ru="К проверке robots.txt",
        sort_score=compute_sort_score("critical", "technical"),
        source_module="advisor.robots",
    )


# ── 3. Schema audit ───────────────────────────────────────────────────


def format_schema_missing(
    schema_type: str,
    pages_missing: int,
    sample_url: str | None,
) -> AdviceCard | None:
    """A specific Schema.org type is missing on money pages.

    We surface as `medium` schema-category — present but not blocking.
    Severity bumps to `high` when the gap is broad (5+ pages without).
    """
    if pages_missing <= 0:
        return None
    severity: Severity = "high" if pages_missing >= 5 else "medium"
    page_word = "страница" if pages_missing == 1 else (
        "страницы" if 2 <= pages_missing <= 4 else "страниц"
    )
    title = (
        f"На {pages_missing} {page_word} нет микроразметки {schema_type}"
    )
    body = (
        f"Schema.org-разметка типа «{schema_type}» помогает Яндексу "
        f"показывать расширенный сниппет (цена, рейтинг, FAQ). "
        f"Сейчас этой разметки нет на {pages_missing} {page_word} — "
        f"в выдаче они выглядят беднее, чем у конкурентов."
    )
    if sample_url:
        body += f" Пример: {sample_url}."
    return AdviceCard(
        id=f"schema:missing_type:{schema_type.lower()}",
        severity=severity,
        category="schema",
        title_ru=title,
        body_ru=body,
        action_ru=(
            f"Открой страницу из списка, прокрути до блока «Микроразметка» "
            f"— там готовая JSON-LD заготовка для {schema_type}. "
            f"Вставь в шаблон страницы и опубликуй."
        ),
        expected_impact_ru=None,
        link=f"/studio/pages?missing_schema={schema_type}",
        cta_ru="Посмотреть страницы",
        sort_score=compute_sort_score(severity, "schema"),
        source_module="advisor.schema",
    )


# ── 4. Keyword match (keyword_gaps from analysis_events) ──────────────


def format_keyword_gaps(
    total_gaps: int,
    total_potential_clicks: int,
    pages_with_gaps: int,
    top_examples: list[dict[str, Any]] | None,
) -> AdviceCard | None:
    """Aggregate keyword_gaps event → one card with potential uplift.

    Severity scales with potential clicks: 500+/mo = high, 100+/mo = medium,
    otherwise low. We never go critical here — keyword adjustments are
    optimisation, not bug-fix.
    """
    if total_gaps <= 0:
        return None
    severity: Severity
    if total_potential_clicks >= 500:
        severity = "high"
    elif total_potential_clicks >= 100:
        severity = "medium"
    else:
        severity = "low"
    gap_word = "дыра" if total_gaps == 1 else (
        "дыры" if 2 <= total_gaps <= 4 else "дыр"
    )
    page_word = "странице" if pages_with_gaps == 1 else "страницах"
    title = (
        f"{total_gaps} {gap_word} по ключевым словам "
        f"на {pages_with_gaps} {page_word}"
    )
    body_parts = [
        f"Сравнили запросы Wordstat с текстом страниц — нашли "
        f"{total_gaps} мест, где в title / H1 / H2 не хватает слов "
        f"из запроса. Это самая дешёвая правка с прогнозируемым "
        f"эффектом: переписать заголовок, и через 2-3 недели позиция "
        f"подтянется.",
    ]
    if top_examples:
        first = top_examples[0]
        q = (first.get("query") or "").strip()
        url = (first.get("page_url") or "").strip()
        if q and url:
            body_parts.append(f"Самый крупный пример: «{q}» — {url}.")
    body = " ".join(body_parts)
    impact_str: str | None = None
    if total_potential_clicks > 0:
        impact_str = f"+{total_potential_clicks} кликов/мес при выходе в топ-5"
    return AdviceCard(
        id="keywords:gaps",
        severity=severity,
        category="keywords",
        title_ru=title,
        body_ru=body,
        action_ru=(
            "Открой «Запросы» → раздел «Слова, которых не хватает». "
            "По каждому пункту есть готовый текст замены для title/H1."
        ),
        expected_impact_ru=impact_str,
        link="/studio/queries?view=keyword_gaps",
        cta_ru="К дырам",
        sort_score=compute_sort_score(
            severity, "keywords",
            expected_clicks_uplift=float(total_potential_clicks),
        ),
        source_module="advisor.keyword_match",
    )


# ── 5. Brain rules (wraps an existing brain Action into an AdviceCard) ─


# Each brain Action carries severity ('critical'|'high'|'medium'|'low')
# — we propagate as-is. The advice category depends on the action id
# prefix: funnel:* → funnel, robots:* → technical (but robots already
# has its own dedicated formatter so won't re-route here), queries:* →
# seo_content, indexation:* → technical, review:* → seo_content,
# outcomes:* → seo_content, wordstat:* → technical, ctr:* → seo_content.

_BRAIN_ACTION_CATEGORY: dict[str, Category] = {
    "funnel": "funnel",
    "robots": "technical",
    "queries": "seo_content",
    "indexation": "technical",
    "review": "seo_content",
    "outcomes": "seo_content",
    "wordstat": "technical",
    "ctr": "seo_content",
    "behavioral": "seo_content",
}


def format_brain_action(action: Any) -> AdviceCard:
    """Convert a brain `Action` (rules.Action) into an AdviceCard.

    Brain actions are already grounded in real DB facts — we trust the
    rule wording verbatim. The only translation is mapping the action's
    id-prefix to an advice `category` so the unified sort places it
    alongside same-class signals.
    """
    aid = action.id or ""
    prefix = aid.split(":", 1)[0]
    category: Category = _BRAIN_ACTION_CATEGORY.get(prefix, "seo_content")
    # Brain severity is one of critical/high/medium/low — never info.
    severity: Severity = action.severity  # type: ignore[assignment]
    return AdviceCard(
        id=f"brain:{aid}",
        severity=severity,
        category=category,
        title_ru=action.title,
        body_ru=action.body_ru,
        action_ru=action.what_to_do_ru,
        expected_impact_ru=None,
        link=action.link_to,
        cta_ru=action.link_label,
        sort_score=compute_sort_score(severity, category),
        source_module="brain",
    )


# ── 6. Wordstat / funnel coverage raw signal ──────────────────────────


def format_funnel_top_raw(
    funnel_top_count: int,
    funnel_top_total_volume: int,
    funnel_top_with_ranking: int,
) -> AdviceCard | None:
    """Raw funnel-top coverage signal (independent of brain).

    The brain has its own `funnel:top_gap` rule that wraps this same
    information — the aggregator dedupes on `id` so only one of the
    two surfaces. This formatter exists so the advice feed has a
    safety net even when the brain rule is silent (e.g. tests that
    poke aggregator directly without going through `build_plan`).

    Stays silent when: <20 funnel_top queries (signal too small), OR
    the site already has top-20 rankings on most of them.
    """
    if funnel_top_count < 20:
        return None
    if funnel_top_with_ranking >= funnel_top_count // 2:
        # Site is already ranking on at least half of funnel-top
        # queries — gap is no longer obvious, downgrade to silent.
        return None
    kmo = max(1, round(funnel_top_total_volume / 1000.0))
    title = (
        f"{funnel_top_count} запросов «верх воронки» на ~{kmo} тыс/мес "
        f"остаются без покрытия"
    )
    body = (
        f"В Wordstat найдены запросы, которыми ищут «что делать» в "
        f"твоём гео — это туристы, которые УЖЕ приехали и просто "
        f"выбирают активность. Самый большой по объёму канал, "
        f"который сейчас не используется."
    )
    expected = f"~{round(funnel_top_total_volume * 0.5 / 1000.0)} тыс посетителей/мес при выходе в топ-10"
    return AdviceCard(
        id="funnel:top_gap_raw",
        severity="high",
        category="funnel",
        title_ru=title,
        body_ru=body,
        action_ru=(
            "Создай 3-5 страниц-лонгридов под запросы верха воронки, "
            "с CTA на коммерческие страницы (бронь экскурсий)."
        ),
        expected_impact_ru=expected,
        link="/studio/queries?layer=funnel_top",
        cta_ru="Посмотреть запросы",
        sort_score=compute_sort_score(
            "high", "funnel",
            expected_clicks_uplift=float(funnel_top_total_volume * 0.5),
        ),
        source_module="advisor.funnel",
    )


# ── 7. Metrica counter health ─────────────────────────────────────────


_METRICA_OK_STATUSES = {"CS_OK", None, ""}


def format_metrica_counter(
    counter_status: str | None,
    counter_code_status: str | None,
) -> AdviceCard | None:
    """Yandex Metrica counter not responding → behavioral data is bad.

    `CS_OK` is the healthy state. Anything else (CS_ERR_*, NOT_INSTALLED)
    means the counter on the site doesn't return the expected payload —
    behavioral metrics are unreliable until the owner fixes it.
    """
    bad = counter_status not in _METRICA_OK_STATUSES or (
        counter_code_status not in _METRICA_OK_STATUSES
        and counter_code_status is not None
    )
    if not bad:
        return None
    status_label = counter_status or counter_code_status or "не определён"
    title = f"Счётчик Метрики не отвечает (status={status_label})"
    body = (
        f"Яндекс.Метрика для этого сайта вернула статус «{status_label}» — "
        f"это значит, что код Метрики на сайте либо не установлен, либо "
        f"не отвечает. Пока не починим — данные о визитах, отказах, "
        f"конверсиях недостоверны, и поведенческие рекомендации работать "
        f"не могут."
    )
    return AdviceCard(
        id="health:metrica_counter",
        severity="high",
        category="health",
        title_ru=title,
        body_ru=body,
        action_ru=(
            "Открой Метрика → Настройки → Код счётчика, проверь, что "
            "он установлен на сайте на каждой странице. После починки "
            "запусти ручной сбор «Метрика» в /studio/activity."
        ),
        expected_impact_ru=None,
        link="/studio/activity",
        cta_ru="К активности",
        sort_score=compute_sort_score("high", "health"),
        source_module="advisor.metrica",
    )


__all__ = [
    "format_health_failure",
    "format_robots_critical",
    "format_schema_missing",
    "format_keyword_gaps",
    "format_brain_action",
    "format_funnel_top_raw",
    "format_metrica_counter",
]
