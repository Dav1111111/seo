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


def _evidence_lines(*items: str | None) -> tuple[str, ...]:
    """Return compact owner-facing evidence lines.

    Keep this as plain strings so the API contract stays simple and the
    frontend can render a stable bullet list without knowing source-
    specific schemas.
    """
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text:
            out.append(text)
    return tuple(out)


def _short(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 1].rstrip()}…"


def _brain_evidence_lines(evidence: dict[str, Any]) -> tuple[str, ...]:
    """Owner-friendly receipt for generic brain-rule evidence.

    Brain actions already have carefully worded title/body/action. This
    block is not the main explanation; it is the small factual receipt
    that proves which counters triggered the rule.
    """
    if not evidence:
        return ()
    labels: dict[str, str] = {
        "pages_total": "Страниц проверено",
        "in_index": "В индексе",
        "not_indexed": "Не в индексе",
        "excluded": "Исключено",
        "unknown": "Статус неизвестен",
        "funnel_top_count": "Запросов верха воронки",
        "funnel_top_total_volume": "Суммарный Wordstat-спрос",
        "funnel_top_pages_count": "Страниц под верх воронки",
        "funnel_warm_count": "Тёплых запросов",
        "direct_product_count": "Прямых продуктовых запросов",
        "pending_recommendations": "Открытых рекомендаций",
        "high_priority": "Высокий приоритет",
        "spam": "Спам-запросов",
        "disputed": "Спорных запросов",
        "total": "Всего запросов",
        "oldest_pending_days": "Самая старая рекомендация, дней",
        "pending_followup": "Ожидают проверки результата",
        "applied_total": "Применено правок",
    }
    lines: list[str] = []
    for key, value in evidence.items():
        if value is None:
            continue
        if key in {"source_finding_id", "signal"}:
            continue
        label = labels.get(key, key.replace("_", " "))
        lines.append(f"{label}: {_short(value, 120)}")
        if len(lines) >= 5:
            break
    return tuple(lines)


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
        why_ru="Стадия полного анализа падала за последние 24 часа.",
        source_ru="Журнал analysis_events",
        target_ru=f"Стадия: {label}",
        evidence_ru=_evidence_lines(
            f"Падений за 24 часа: {count}",
            f"Последняя ошибка: {_short(last_message, 180)}" if last_message else None,
        ),
        verification_ru=(
            "После исправления перезапусти полный анализ: эта карточка "
            "исчезнет, когда стадия перестанет падать в свежем прогоне."
        ),
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
        why_ru="robots.txt содержит критичные правила или не читается как ожидается.",
        source_ru="Последний аудит robots.txt",
        target_ru="robots.txt для YandexBot",
        evidence_ru=_evidence_lines(
            f"Критических проблем: {critical_issues}",
            f"Файл валиден для Яндекса: {'да' if valid_for_yandex else 'нет'}",
        ),
        verification_ru=(
            "После правки снова запусти аудит индексации: критических "
            "проблем должно стать 0."
        ),
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
        why_ru=(
            f"На деньгах/посадочных страницах не найден тип Schema.org "
            f"{schema_type}."
        ),
        source_ru="Page review / schema audit",
        target_ru=f"{pages_missing} страниц без {schema_type}",
        evidence_ru=_evidence_lines(
            f"Отсутствующий тип: {schema_type}",
            f"Страниц с проблемой: {pages_missing}",
            f"Пример URL: {sample_url}" if sample_url else None,
        ),
        verification_ru=(
            "После публикации запусти глубокий разбор страницы: тип "
            f"{schema_type} должен появиться в блоке Schema.org."
        ),
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
        why_ru="Запросы из Wordstat слабо представлены в title / H1 / H2 страниц.",
        source_ru="Keyword gaps: Wordstat + текст страниц",
        target_ru=f"{pages_with_gaps} страниц с keyword-gap",
        evidence_ru=_evidence_lines(
            f"Всего дыр: {total_gaps}",
            f"Страниц затронуто: {pages_with_gaps}",
            (
                f"Расчётный потенциал: +{total_potential_clicks} кликов/мес "
                "при целевом сценарии"
                if total_potential_clicks > 0 else None
            ),
            (
                f"Пример: «{top_examples[0].get('query')}» → "
                f"{top_examples[0].get('page_url')}"
                if top_examples else None
            ),
        ),
        verification_ru=(
            "После правки title/H1/H2 отметь совет как применённый: "
            "через 14 дней система сравнит Webmaster-показы/позиции с baseline."
        ),
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
    # SERP-driven competitor signals are competitive intelligence, not
    # technical defects — route to "funnel" so they sort alongside
    # other demand/competitor advice.
    "serp": "funnel",
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
    examples = getattr(action, "examples", None) or []
    first_example = examples[0] if examples else None
    target = None
    if isinstance(first_example, dict):
        target = (
            first_example.get("label")
            or first_example.get("url")
            or first_example.get("query")
        )
    evidence = getattr(action, "evidence", None)
    evidence_lines = _brain_evidence_lines(evidence if isinstance(evidence, dict) else {})
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
        why_ru="Правило Brain сработало на свежем снимке данных сайта.",
        source_ru="Brain snapshot: индексация, запросы, рекомендации и события анализа",
        target_ru=target or action.link_to,
        evidence_ru=evidence_lines,
        verification_ru=(
            "После выполнения отметь совет как применённый: система "
            "зафиксирует baseline и проверит изменение через 14 дней."
        ),
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
    expected = (
        f"верхняя оценка спроса Wordstat: ~"
        f"{round(funnel_top_total_volume * 0.5 / 1000.0)} тыс/мес "
        "при топ-10, не прогноз кликов"
    )
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
        why_ru=(
            "В базе есть много запросов верхней воронки, но сайт почти "
            "не ранжируется по ним в топ-20."
        ),
        source_ru="Wordstat + Webmaster/query_performance",
        target_ru="Слой запросов: верх воронки",
        evidence_ru=_evidence_lines(
            f"Запросов funnel_top: {funnel_top_count}",
            f"Суммарный Wordstat-спрос: {funnel_top_total_volume}/мес",
            f"Запросов с позицией в топ-20: {funnel_top_with_ranking}",
        ),
        verification_ru=(
            "После создания/доработки страниц проверяем через Webmaster: "
            "должны появиться показы и позиции по запросам funnel_top."
        ),
    )


# ── 6b. SERP-derived competitor dominance ────────────────────────────


def format_competitor_dominance(
    *,
    domain: str,
    top3_count: int,
    top10_count: int,
    total_probed_queries: int,
    share_of_queries: float,
    sample_queries: list[str] | None = None,
) -> AdviceCard | None:
    """Top competitor in our SERPs → owner-facing card.

    Mirrors the brain rule's wording but exposed as a standalone
    formatter so the aggregator (or tests) can build the same card
    without re-running the rule layer. Stays silent if no real
    dominance (share below ~30% threshold) or inputs are missing.
    """
    if not domain or total_probed_queries <= 0:
        return None
    share = float(share_of_queries or 0.0)
    if share < 0.30:
        return None
    samples = list(sample_queries or [])

    if share >= 0.60:
        severity: Severity = "critical"
    elif share >= 0.40:
        severity = "high"
    else:
        severity = "medium"

    share_pct = int(round(share * 100))
    title = (
        f"{domain} держит топ-3 по {top3_count} из {total_probed_queries} "
        f"ценных запросов ({share_pct}%)"
    )
    body = (
        f"По данным еженедельной выборки SERP, {domain} стабильно "
        f"оказывается в топ-3 Яндекса по большой доле твоих ценных "
        f"запросов. Это означает, что Яндекс видит именно их сильнее "
        f"тебя по теме — самое полезное действие сейчас — изучить, "
        f"что у них на посадочных страницах есть, чего нет у тебя."
    )
    action = (
        f"Открой «Конкуренты», добавь {domain} (если ещё нет) и "
        f"запусти глубокий разбор. Сравни длину текста, наличие "
        f"цены/бронирования, фото, H2-структуру и закрой пробелы на "
        f"своих конкурирующих страницах."
    )
    evidence_lines = _evidence_lines(
        f"Top-3 присутствий: {top3_count} из {total_probed_queries}",
        f"Доля топ-3: {share_pct}%",
        f"В топ-10 хотя бы раз: {top10_count}",
        (f"Пример запросов: {', '.join(samples[:3])}" if samples else None),
    )
    return AdviceCard(
        id=f"serp:competitor_dominates:{domain}",
        severity=severity,
        category="funnel",
        title_ru=title,
        body_ru=body,
        action_ru=action,
        expected_impact_ru=None,
        link="/studio/competitors",
        cta_ru="К конкурентам",
        sort_score=compute_sort_score(severity, "funnel"),
        source_module="advisor.serp",
        why_ru=(
            "Еженедельная выборка SERP по самым ценным запросам показала "
            "одного и того же конкурента в топ-3 — это сильный сигнал."
        ),
        source_ru="query_serp_snapshots (weekly probe)",
        target_ru=domain,
        evidence_ru=evidence_lines,
        verification_ru=(
            "После доработки своих страниц жди следующего еженедельного "
            "опроса SERP — позиция и распределение конкурентов обновятся."
        ),
    )


# ── 7. Query coverage action cards ───────────────────────────────────


def format_query_action(
    *,
    query_id: str,
    query_text: str,
    relevance: str,
    wordstat_volume: int | None,
    last_position: float | None,
    strategy_code: str,
    strategy_label_ru: str,
    strategy_action_ru: str,
    coverage_status: str,
    coverage_score: int,
    coverage_reason_ru: str,
    coverage_action_ru: str,
    best_page_id: str | None,
    best_page_url: str | None,
    best_page_title: str | None,
) -> AdviceCard | None:
    """One concrete query → one concrete owner action.

    Unlike aggregate funnel cards, this points to a specific phrase and
    usually a specific page candidate. It stays silent when coverage is
    already strong or the query is intentionally ignored.
    """
    if relevance in {"spam", "out_of_market"}:
        return None
    if coverage_status not in {"missing", "weak"}:
        return None

    volume = int(wordstat_volume or 0)
    category: Category = "funnel" if relevance in {
        "funnel_warm", "funnel_top", "adjacent",
    } else "keywords"

    if relevance in {"direct_product", "own"}:
        severity: Severity = "high"
    elif strategy_code == "mention_as_alternative":
        severity = "medium" if volume < 1000 else "high"
    elif relevance == "funnel_top":
        severity = "medium" if volume < 1500 else "high"
    else:
        severity = "medium" if volume < 800 else "high"

    if strategy_code == "mention_as_alternative":
        title = f"Запрос «{query_text}» лучше встроить как альтернативу"
    elif coverage_status == "missing":
        title = f"Запрос «{query_text}» не закрыт страницей"
    else:
        title = f"Запрос «{query_text}» закрыт слабо"

    facts: list[str] = []
    if volume > 0:
        facts.append(f"Wordstat: {volume}/мес")
    if last_position is not None:
        facts.append(f"позиция: {last_position:.1f}")
    facts.append(f"покрытие: {coverage_score}/100")

    page_part = ""
    if best_page_url:
        label = best_page_title or best_page_url
        page_part = f" Лучший кандидат: {label}."

    body = (
        f"{'; '.join(facts)}. {coverage_reason_ru}."
        f"{page_part} Это конкретная связка «совет → запрос → страница», "
        "а не общий SEO-совет."
    )

    action = coverage_action_ru or strategy_action_ru
    if strategy_code == "mention_as_alternative":
        action = strategy_action_ru

    if best_page_id:
        link = f"/studio/pages/{best_page_id}"
        cta = "Открыть страницу"
    else:
        link = f"/studio/queries?layer={relevance}"
        cta = "Открыть запросы"

    expected = None
    if volume > 0:
        expected = f"потенциал спроса Wordstat: {volume}/мес, не прогноз кликов"

    # Conservative sorting lift: high-volume query actions bubble
    # within their severity, but cannot outrank critical technical bugs.
    sort_lift = min(float(volume) * 0.05, 1500.0)

    return AdviceCard(
        id=f"query_action:{query_id}",
        severity=severity,
        category=category,
        title_ru=title,
        body_ru=body,
        action_ru=action,
        expected_impact_ru=expected,
        link=link,
        cta_ru=cta,
        sort_score=compute_sort_score(
            severity,
            category,
            expected_clicks_uplift=sort_lift,
        ),
        source_module="advisor.query_coverage",
        why_ru="Запрос релевантен бизнесу, но покрытие сайта по нему слабое или отсутствует.",
        source_ru="Wordstat + Webmaster + deterministic page coverage",
        target_ru=(
            f"Запрос: «{query_text}»"
            + (f" · страница: {best_page_url}" if best_page_url else "")
        ),
        evidence_ru=_evidence_lines(
            f"Релевантность: {relevance}",
            f"Стратегия: {strategy_label_ru}",
            f"Wordstat: {volume}/мес" if volume > 0 else "Wordstat: данных по объёму нет",
            f"Последняя позиция: {last_position:.1f}" if last_position is not None else None,
            f"Покрытие страницы: {coverage_score}/100 ({coverage_status})",
            coverage_reason_ru,
        ),
        verification_ru=(
            "После правки отмечаем карточку как применённую: через 14 "
            "дней сравниваем позицию/показы по этому запросу и обновляем "
            "coverage, чтобы старый совет не повторялся."
        ),
    )


# ── 8. Metrica counter health ─────────────────────────────────────────


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
        why_ru="Без живой Метрики поведенческие и конверсионные советы недостоверны.",
        source_ru="Yandex Metrica counter status в daily_metrics",
        target_ru="Счётчик Яндекс.Метрики",
        evidence_ru=_evidence_lines(
            f"counter_status: {counter_status or 'нет данных'}",
            f"counter_code_status: {counter_code_status or 'нет данных'}",
        ),
        verification_ru=(
            "После установки кода перезапусти сбор Метрики: статус должен "
            "стать CS_OK, а визиты начнут попадать в отчёты."
        ),
    )


# ── 9. SERP-intel per-query gap ────────────────────────────────────────


def format_serp_gap(
    *,
    query_text: str,
    wordstat_volume: int,
    our_position: int | None,
    top_competitor_domain: str,
    top_competitor_url: str,
    site_id: Any,
    query_id: Any,
) -> AdviceCard | None:
    """One card per query where we're outside top-5 but the same
    competitor sits in top-3 — and the demand is non-trivial.

    Filters (all must hold):
      * our_position is None OR > 5
      * top_competitor_domain is non-empty
      * wordstat_volume >= 50 (below this the Wordstat signal is noisy
        and the SERP page may be partly bot-traffic)

    Otherwise → None. The aggregator picks the top-5 of these by
    expected uplift so the home feed stays focused.

    Severity:
      high   → wordstat_volume >= 500
      medium → wordstat_volume >= 100
      low    → wordstat_volume >= 50
    """
    if wordstat_volume < 50:
        return None
    if our_position is not None and our_position <= 5:
        return None
    competitor = (top_competitor_domain or "").strip()
    if not competitor:
        return None

    if wordstat_volume >= 500:
        severity: Severity = "high"
    elif wordstat_volume >= 100:
        severity = "medium"
    else:
        severity = "low"

    position_label = (
        f"мы {our_position}-е место"
        if our_position is not None
        else "нас в топ-10 нет"
    )
    title = (
        f"«{_short(query_text, 80)}» — {position_label}, "
        f"в топ-3 сидит {competitor}"
    )
    body = (
        f"По запросу «{query_text}» Wordstat показывает ~{wordstat_volume}/мес "
        f"и {competitor} стабильно держится в топ-3. "
        f"{position_label.capitalize()} — это ощутимая дыра в видимости "
        f"на запрос, релевантный твоему бизнесу."
    )
    action = (
        f"Открой запрос в разделе «Запросы», посмотри карточку SERP — "
        f"там видна целевая страница конкурента ({_short(top_competitor_url, 120) or competitor}). "
        f"Сравни её со своей по контенту, цене, отзывам и H2; "
        f"добавь то, чего у тебя нет."
    )

    # Sort-lift: small fractional bump per volume so high-volume gaps
    # bubble within their severity (capped to never overpower critical
    # technical cards).
    sort_lift = min(float(wordstat_volume) * 0.05, 1500.0)

    return AdviceCard(
        id=f"serp:gap:{query_id}",
        severity=severity,
        category="funnel",
        title_ru=title,
        body_ru=body,
        action_ru=action,
        expected_impact_ru=(
            f"потенциал спроса Wordstat: {wordstat_volume}/мес, не прогноз кликов"
        ),
        link=f"/studio/queries?focus={query_id}",
        cta_ru="Открыть запрос",
        sort_score=compute_sort_score(
            severity, "funnel", expected_clicks_uplift=sort_lift,
        ),
        source_module="advisor.serp_intel",
        why_ru=(
            "По важному запросу мы вне топ-5, а один и тот же конкурент "
            "стоит в топ-3."
        ),
        source_ru="query_serp_snapshots (weekly Yandex Cloud Search probe)",
        target_ru=f"Запрос: «{query_text}»",
        evidence_ru=_evidence_lines(
            f"Wordstat: {wordstat_volume}/мес",
            f"Наша позиция: {our_position if our_position is not None else 'вне топ-10'}",
            f"Топ-3 конкурент: {competitor}",
            f"URL конкурента: {top_competitor_url}" if top_competitor_url else None,
        ),
        verification_ru=(
            "После правки страница пересобирается через 14 дней: "
            "если позиция выросла или мы вошли в топ-5 — карточка уйдёт."
        ),
    )


__all__ = [
    "format_health_failure",
    "format_robots_critical",
    "format_schema_missing",
    "format_keyword_gaps",
    "format_brain_action",
    "format_funnel_top_raw",
    "format_query_action",
    "format_metrica_counter",
    "format_serp_gap",
]
