"""Layer 2 — turn structured findings into Recommendation rows with Russian text.

Separation of concerns: checks produce facts, composer produces prose.
Templates are keyed by signal_type and use evidence fields for substitution.

Every Recommendation carries a `source_finding_id` so the LLM enrichment
layer (Step 4) can merge rewrites deterministically.

Composer emits ONE Recommendation per fail/warn finding. Passed and
not_applicable findings are dropped (they show as summary stats).
"""

from __future__ import annotations

from app.core_audit.review.dto import Recommendation
from app.core_audit.review.enums import RecCategory, RecPriority
from app.core_audit.review.findings import CheckFinding, FindingStatus
from app.core_audit.review.llm.base import finding_id


SIGNAL_CATEGORY: dict[str, RecCategory] = {
    "title_length": RecCategory.title,
    "title_keyword_repetition": RecCategory.title,
    "title_missing": RecCategory.title,
    "h1_missing": RecCategory.h1_structure,
    "h1_equals_title": RecCategory.h1_structure,
    "density_title": RecCategory.over_optimization,
    "density_h1": RecCategory.over_optimization,
    "density_body": RecCategory.over_optimization,
    "missing_critical_h2": RecCategory.h1_structure,
    "missing_recommended_h2": RecCategory.h1_structure,
    "schema_missing": RecCategory.schema,
    "schema_types_recommended": RecCategory.schema,
    "schema_types_complete": RecCategory.schema,
    "schema_missing_type": RecCategory.schema,
    "schema_cargo_cult_present": RecCategory.schema,
    "eeat_signal_missing": RecCategory.eeat,
    "eeat_signal_present": RecCategory.eeat,
    "eeat_signals_missing": RecCategory.eeat,
    "commercial_factor_missing": RecCategory.commercial,
    "commercial_factor_present": RecCategory.commercial,
    "commercial_factor_deferred_to_llm": RecCategory.commercial,
    "commercial_factors_missing": RecCategory.commercial,
    "over_optimization_stuffing": RecCategory.over_optimization,
}


def compose(findings: list[CheckFinding]) -> list[Recommendation]:
    """Emit one Recommendation per fail/warn finding. Drop pass/NA."""
    out: list[Recommendation] = []
    for f in findings:
        if f.status not in (FindingStatus.fail, FindingStatus.warn):
            continue
        rec = _compose_one(f)
        if rec is not None:
            out.append(rec)
    return out


def _compose_one(f: CheckFinding) -> Recommendation | None:
    handler = _HANDLERS.get(f.signal_type)
    if handler is None:
        return None
    return handler(f)


def _priority(f: CheckFinding) -> RecPriority:
    if f.severity is None:
        return RecPriority.medium
    try:
        return RecPriority(f.severity)
    except ValueError:
        return RecPriority.medium


def _category(f: CheckFinding) -> RecCategory:
    return SIGNAL_CATEGORY.get(f.signal_type, RecCategory.title)


def _rec(f: CheckFinding, reasoning_ru: str, *,
         before: str | None = None, after: str | None = None) -> Recommendation:
    return Recommendation(
        category=_category(f),
        priority=_priority(f),
        reasoning_ru=reasoning_ru,
        before=before,
        after=after,
        source_finding_id=finding_id(f),
    )


# ── Per-signal composers ──────────────────────────────────────────────

def _title_length(f: CheckFinding) -> Recommendation:
    length = f.evidence.get("length", 0)
    return _rec(f, (
        f"Длина title {length} символов — Яндекс обрезает сниппет около "
        f"70 символов для кириллицы. Сократите до ≤65 символов, оставив "
        f"ключевую фразу в начале."
    ))


def _title_keyword_repetition(f: CheckFinding) -> Recommendation:
    count = f.evidence.get("keyword_count", 0)
    return _rec(f, (
        f"Ключевое слово повторяется в title {count} раз(а). "
        f"Яндекс-фильтр «Баден-Баден» может снизить страницу за переоптимизацию. "
        f"Оставьте одно упоминание + добавьте модификаторы."
    ))


def _title_missing(f: CheckFinding) -> Recommendation:
    return _rec(f, (
        "У страницы нет тега title. Это критично для индексации в Яндексе — "
        "добавьте title до 65 символов с ключевой фразой в начале и брендом в конце."
    ))


def _h1_missing(f: CheckFinding) -> Recommendation:
    return _rec(f, (
        "На странице отсутствует H1. H1 обязателен для корректной индексации "
        "и должен содержать ключевую фразу в естественной формулировке, "
        "не дублируя title дословно."
    ))


def _h1_equals_title(f: CheckFinding) -> Recommendation:
    return _rec(f, (
        "H1 дословно совпадает с title — вы теряете возможность охватить "
        "дополнительные ключевые слова. Сделайте H1 естественной формулировкой "
        "(title — для SERP, H1 — для читателя)."
    ), before=f.evidence.get("h1"))


def _density_scope(f: CheckFinding) -> Recommendation:
    density = f.evidence.get("density", 0.0)
    scope = f.signal_type.replace("density_", "")
    if f.evidence.get("under_optimization"):
        reasoning = (
            f"Плотность ключевого слова в {scope} всего {density*100:.2f}% — "
            f"под коммерческий интент этого мало. Упомяните ключевую фразу "
            f"в title, H1 и первом абзаце естественным образом."
        )
    else:
        reasoning = (
            f"Плотность ключевого слова в {scope} — {density*100:.2f}%. "
            f"Риск фильтра «Баден-Баден». Разбавьте синонимами и уберите повторы."
        )
    return _rec(f, reasoning)


def _missing_h2_block(f: CheckFinding) -> Recommendation:
    block = f.evidence.get("block", "")
    tier = f.evidence.get("tier", "recommended")
    tier_ru = "обязательный" if tier == "critical" else "рекомендуемый"
    return _rec(f, (
        f"На странице не хватает {tier_ru} H2-раздела «{block}». "
        f"Для этого типа страницы Яндекс ожидает тематическую полноту — "
        f"добавьте блок с ответом на соответствующий пользовательский вопрос."
    ))


def _schema_missing(f: CheckFinding) -> Recommendation:
    types = f.evidence.get("recommended_types", [])
    types_str = ", ".join(types)
    return _rec(f, (
        f"На странице отсутствует Schema.org разметка. Для текущего интента "
        f"рекомендуется: {types_str}. Для туров не заменяйте корректный "
        f"TouristTrip/Service на Product автоматически: важнее передать "
        f"Offer/AggregateOffer с ценой, валютой и ссылкой."
    ))


# Severity → owner-facing Russian label for the per-type missing card.
_SCHEMA_SEVERITY_LABEL_RU: dict[str, str] = {
    "critical": "критично",
    "high": "важно",
    "medium": "желательно",
    "low": "опционально",
}


def _schema_missing_type(f: CheckFinding) -> Recommendation:
    """One paste-in recommendation per recommended Schema.org type the page
    is currently missing. Renders a deterministic, LLM-free template card.

    Evidence shape (set in schema_checks.check_schema):
      missing_type           — bare schema.org class name (e.g. "FAQPage")
      present_types          — list of normalized types we DID find
                               (alias: `schema_types_present`, both populated)
      recommended_types      — full list of recommended-for-intent types
      intent                 — IntentCode.value string
      example_jsonld         — paste-in template (may be absent for rare types)
      rationale_ru           — short Russian explanation of the visible
                               Yandex effect of this schema type
    """
    missing_type = f.evidence.get("missing_type", "")
    intent = f.evidence.get("intent", "")
    present = (
        f.evidence.get("present_types")
        or f.evidence.get("schema_types_present")
        or []
    )
    example = f.evidence.get("example_jsonld")
    rationale_ru = f.evidence.get("rationale_ru") or ""
    severity_label = _SCHEMA_SEVERITY_LABEL_RU.get(f.severity or "medium", "желательно")

    present_str = (
        ", ".join(present)
        if present
        else "ничего из ожидаемой Schema.org разметки"
    )
    reasoning_parts = [
        f"Не хватает разметки {missing_type} для интента «{intent}» "
        f"({severity_label})."
    ]
    if rationale_ru:
        reasoning_parts.append(rationale_ru)
    reasoning_parts.append(
        f"На странице сейчас найдено: {present_str}. Добавьте "
        f"{missing_type} отдельным `<script type=\"application/ld+json\">` "
        f"в `<head>` или перед `</body>`."
    )
    reasoning = " ".join(reasoning_parts)
    before = "На странице найдено: " + present_str if present else None
    after = (
        f"Добавьте Schema.org {missing_type}. Пример JSON-LD:\n\n"
        f"```json\n{example}\n```"
    ) if example else (
        f"Добавьте Schema.org {missing_type} в формате JSON-LD."
    )
    return _rec(f, reasoning, before=before, after=after)


# Human-readable labels for EEAT signal slugs. Lives here (not in the
# profile) because composer prose is locale-specific and the profile
# stays language-agnostic. Keep keys aligned with profile signal names.
_EEAT_LABELS_RU: dict[str, str] = {
    "rto_number": "номер в реестре туроператоров (РТО)",
    "inn": "ИНН",
    "ogrn": "ОГРН",
    "license_section": "упоминание лицензии / свидетельства",
    "author_byline": "подпись автора материала",
    "reviews_block": "блок отзывов клиентов",
    "yandex_maps_reviews": "ссылка на отзывы на Яндекс.Картах",
}


def _eeat_signal_missing(f: CheckFinding) -> Recommendation:
    """Legacy per-signal composer — kept for back-compat with any code path
    still emitting the granular finding. New paths use the aggregate
    `eeat_signals_missing` finding handled below."""
    name = f.evidence.get("signal_name", "signal")
    human = _EEAT_LABELS_RU.get(name, name)
    return _rec(f, (
        f"Детектор не нашёл на странице: {human}. Это E-E-A-T сигнал для "
        f"Yandex Proksima. Если он действительно отсутствует — добавьте; "
        f"если есть, но в другой форме — проверьте вёрстку и формат."
    ))


def _eeat_signals_missing(f: CheckFinding) -> Recommendation:
    """Aggregate composer — one card listing every missing EEAT signal.

    `before_text` enumerates what the detector did NOT find on the page
    (comma list of Russian labels). `after_text` is a bulleted Markdown
    list the owner can paste into the «О нас» / footer block.
    """
    items: list[str] = list(f.evidence.get("missing_items") or [])
    labels = [_EEAT_LABELS_RU.get(name, name) for name in items]
    before = "Не вижу на странице: " + ", ".join(labels) if labels else None
    after_lines = ["Доверие компании — добавьте блок легальности:"] + [
        f"- {label}" for label in labels
    ]
    after = "\n".join(after_lines) if labels else None
    reasoning = (
        f"Не нашёл блок легальности ({len(labels)} элемент(а)). "
        f"Yandex Proksima использует эти сигналы для оценки E-E-A-T. "
        f"Соберите всё в один блок в футере или на странице «О нас»."
    )
    return _rec(f, reasoning, before=before, after=after)


def _commercial_factor_missing(f: CheckFinding) -> Recommendation:
    """Legacy per-factor composer — see note in `_eeat_signal_missing`."""
    return _rec(f, (
        f"Не обнаружен коммерческий фактор: "
        f"{f.evidence.get('description_ru', f.evidence.get('factor_name'))}. "
        f"Коммерческие факторы — явный сигнал ранжирования в Яндексе. "
        f"Проверьте, отображается ли фактор на странице."
    ))


def _commercial_factors_missing(f: CheckFinding) -> Recommendation:
    """Aggregate composer for commercial factors. Mirrors `_eeat_signals_missing`."""
    items: list[str] = list(f.evidence.get("missing_items") or [])
    descriptions: list[str] = list(f.evidence.get("missing_descriptions") or [])
    # Prefer description_ru (human-grade phrasing baked into the profile)
    # and fall back to the slug if a profile entry didn't supply one.
    labels: list[str] = []
    for idx, name in enumerate(items):
        desc = descriptions[idx] if idx < len(descriptions) and descriptions[idx] else name
        labels.append(desc)
    before = "Не вижу на странице: " + ", ".join(labels) if labels else None
    after_lines = ["Коммерческие сигналы — добавьте на страницу:"] + [
        f"- {label}" for label in labels
    ]
    after = "\n".join(after_lines) if labels else None
    reasoning = (
        f"Не нашёл коммерческие факторы ({len(labels)} штук(и)). "
        f"Это явный сигнал ранжирования в Яндексе для коммерческих "
        f"запросов. Проверьте, что эти элементы действительно "
        f"отображаются на странице."
    )
    return _rec(f, reasoning, before=before, after=after)


def _over_optimization_stuffing(f: CheckFinding) -> Recommendation:
    density = f.evidence.get("density_body", 0.0)
    tkc = f.evidence.get("title_keyword_count", 0)
    return _rec(f, (
        f"Переоптимизация: плотность в body {density*100:.2f}% + "
        f"{tkc} повторов в title. Высокий риск фильтра «Баден-Баден». "
        f"Перепишите раздел с введением синонимов и нарастанием тематической "
        f"глубины вместо повторов одной фразы."
    ))


_HANDLERS = {
    "title_length": _title_length,
    "title_keyword_repetition": _title_keyword_repetition,
    "title_missing": _title_missing,
    "h1_missing": _h1_missing,
    "h1_equals_title": _h1_equals_title,
    "density_title": _density_scope,
    "density_h1": _density_scope,
    "density_body": _density_scope,
    "missing_critical_h2": _missing_h2_block,
    "missing_recommended_h2": _missing_h2_block,
    "schema_missing": _schema_missing,
    "schema_missing_type": _schema_missing_type,
    "eeat_signal_missing": _eeat_signal_missing,
    "eeat_signals_missing": _eeat_signals_missing,
    "commercial_factor_missing": _commercial_factor_missing,
    "commercial_factors_missing": _commercial_factors_missing,
    "over_optimization_stuffing": _over_optimization_stuffing,
}
