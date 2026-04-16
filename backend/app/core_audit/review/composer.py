"""Layer 2 — turn structured findings into Recommendation rows with Russian text.

Separation of concerns (user requirement 3): checks produce facts,
composer produces prose. Templates are keyed by signal_type and use
evidence fields for substitution.

Composer emits ONE Recommendation per fail/warn finding. Passed and
not_applicable findings are dropped (they show as stats on the summary,
not as action items).
"""

from __future__ import annotations

from typing import Any

from app.core_audit.review.dto import Recommendation
from app.core_audit.review.enums import RecCategory, RecPriority
from app.core_audit.review.findings import CheckFinding, FindingStatus


# Map signal_type → category. Composer uses this to route Recommendations
# into the UI's category tabs.
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
    "schema_cargo_cult_present": RecCategory.schema,
    "eeat_signal_missing": RecCategory.eeat,
    "eeat_signal_present": RecCategory.eeat,
    "commercial_factor_missing": RecCategory.commercial,
    "commercial_factor_present": RecCategory.commercial,
    "commercial_factor_deferred_to_llm": RecCategory.commercial,
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


# ── Per-signal composers ──────────────────────────────────────────────

def _title_length(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    length = ev.get("length", 0)
    return Recommendation(
        category=_category(f),
        priority=_priority(f),
        reasoning_ru=(
            f"Длина title {length} символов — Яндекс обрезает сниппет "
            f"около 70 символов для кириллицы. Сократите до ≤65 символов, "
            f"оставив ключевую фразу в начале."
        ),
        evidence=ev if False else None,  # placeholder
        before=None, after=None,
    )


def _title_keyword_repetition(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    count = ev.get("keyword_count", 0)
    return Recommendation(
        category=_category(f),
        priority=_priority(f),
        reasoning_ru=(
            f"Ключевое слово повторяется в title {count} раз(а). "
            f"Яндекс-фильтр «Баден-Баден» может снизить страницу за переоптимизацию. "
            f"Оставьте одно упоминание + добавьте модификаторы (длительность, цена, формат)."
        ),
        before=None, after=None,
    )


def _title_missing(f: CheckFinding) -> Recommendation:
    return Recommendation(
        category=_category(f), priority=RecPriority.critical,
        reasoning_ru=(
            "У страницы нет тега title. Это критично для индексации в Яндексе — "
            "добавьте title до 65 символов с ключевой фразой в начале и брендом в конце."
        ),
        before=None, after=None,
    )


def _h1_missing(f: CheckFinding) -> Recommendation:
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            "На странице отсутствует H1. H1 обязателен для корректной индексации "
            "и должен содержать ключевую фразу в естественной формулировке, "
            "не дублируя title дословно."
        ),
        before=None, after=None,
    )


def _h1_equals_title(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            "H1 дословно совпадает с title — вы теряете возможность охватить "
            "дополнительные ключевые слова. Сделайте H1 естественной человеческой "
            "формулировкой (title — для SERP, H1 — для читателя)."
        ),
        before=ev.get("h1"), after=None,
    )


def _density_scope(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    density = ev.get("density", 0.0)
    scope = f.signal_type.replace("density_", "")
    if ev.get("under_optimization"):
        reasoning = (
            f"Плотность ключевого слова в {scope} всего {density*100:.2f}% — "
            f"под коммерческий интент этого мало. Упомяните ключевую фразу "
            f"в title, H1 и первом абзаце body естественным образом."
        )
    else:
        reasoning = (
            f"Плотность ключевого слова в {scope} — {density*100:.2f}%. "
            f"Риск фильтра «Баден-Баден». Разбавьте синонимами и уберите повторы."
        )
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=reasoning, before=None, after=None,
    )


def _missing_h2_block(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    block = ev.get("block", "")
    tier = ev.get("tier", "recommended")
    tier_ru = "обязательный" if tier == "critical" else "рекомендуемый"
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            f"На странице не хватает {tier_ru} H2-раздела «{block}». "
            f"Для этого типа страницы Яндекс ожидает тематическую полноту — "
            f"добавьте блок с ответом на соответствующий пользовательский вопрос."
        ),
        before=None, after=None,
    )


def _schema_missing(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    types = ev.get("recommended_types", [])
    types_str = ", ".join(types)
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            f"На странице отсутствует Schema.org разметка. Для текущего интента "
            f"рекомендуется: {types_str}. Не используйте TouristTrip / "
            f"TouristAttraction — Яндекс их не парсит в расширенные сниппеты."
        ),
        before=None, after=None,
    )


def _eeat_signal_missing(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    name = ev.get("signal_name", "signal")
    human = {
        "rto_number": "номер в реестре туроператоров (РТО)",
        "inn": "ИНН",
        "ogrn": "ОГРН",
        "license_section": "упоминание лицензии / свидетельства",
        "author_byline": "подпись автора материала",
        "reviews_block": "блок отзывов клиентов",
        "yandex_maps_reviews": "ссылка на отзывы на Яндекс.Картах",
    }.get(name, name)
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            f"Детектор не нашёл на странице: {human}. Это E-E-A-T сигнал, важный "
            f"для Yandex Proksima. Если он действительно отсутствует — добавьте; "
            f"если есть, но в другой форме — проверьте верстку и формат."
        ),
        before=None, after=None,
    )


def _commercial_factor_missing(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            f"Не обнаружен коммерческий фактор: {ev.get('description_ru', ev.get('factor_name'))}. "
            f"Коммерческие факторы — явный сигнал ранжирования в Яндексе. "
            f"Проверьте, отображается ли фактор на странице и попадает ли в контент-блок."
        ),
        before=None, after=None,
    )


def _over_optimization_stuffing(f: CheckFinding) -> Recommendation:
    ev = f.evidence
    density = ev.get("density_body", 0.0)
    tkc = ev.get("title_keyword_count", 0)
    return Recommendation(
        category=_category(f), priority=_priority(f),
        reasoning_ru=(
            f"Переоптимизация: плотность в body {density*100:.2f}% + "
            f"{tkc} повторов в title. Высокий риск фильтра «Баден-Баден». "
            f"Перепишите раздел с введением синонимов и нарастанием тематической "
            f"глубины вместо повторов одной фразы."
        ),
        before=None, after=None,
    )


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
    "eeat_signal_missing": _eeat_signal_missing,
    "commercial_factor_missing": _commercial_factor_missing,
    "over_optimization_stuffing": _over_optimization_stuffing,
}
