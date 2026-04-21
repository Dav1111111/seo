"""Growth Opportunities — turn competitor findings into actions.

Takes the outputs of content_gap + deep_dive and produces a small list
of concrete opportunities a site owner can act on this week.

Sources of opportunities
------------------------
1. Content gaps. For each cluster of similar queries where competitors
   are top-5 and the site isn't in top-30, produce one "create/expand a
   page about X" opportunity. Groups queries that share a normalised
   head-term so "багги абхазия" / "багги в абхазия" / "багги туры
   абхазия" fold into one opportunity, not three.

2. Feature diff. For each boolean signal the deep-dive extractor
   checks (has_booking_cta, has_reviews, has_whatsapp, …), if at least
   half of the crawled competitors have it and the site doesn't, emit
   a "copy this feature" opportunity.

3. Schema diff. If any competitor has AggregateRating / Product /
   Service / FAQPage schema and the site doesn't, emit one high-
   priority opportunity per missing high-value type.

Output
------
A plain list of dicts (JSONB-safe). No DB writes — caller is the
deep-dive task, which persists under target_config.growth_opportunities.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections import Counter
from typing import Iterable, Sequence


# Features we track in deep-dive and how to phrase them.
FEATURE_LABELS: dict[str, tuple[str, str]] = {
    # key -> (short action, full action sentence)
    "has_booking_cta": (
        "Добавь кнопку «Забронировать»",
        "Поставь видимую кнопку брони или заявки на главной и ключевых "
        "страницах услуг — это самый частый commercial-фактор в твоей нише.",
    ),
    "has_reviews": (
        "Покажи отзывы клиентов",
        "Выведи отзывы/рейтинг с примером имени и датой. Подключи "
        "Schema.org AggregateRating — это даёт звёзды прямо в поиске.",
    ),
    "has_whatsapp": (
        "Добавь ссылку на WhatsApp",
        "Добавь wa.me / whatsapp-ссылку в шапку. Ни у кого из твоих "
        "конкурентов её нет — можешь занять канал первым.",
    ),
    "has_telegram": (
        "Добавь ссылку на Telegram",
        "Подключи t.me/username в шапку и в контакты. Большинство "
        "конкурентов уже это сделали.",
    ),
    "has_phone": (
        "Выведи телефон на видное место",
        "Телефон должен быть в шапке и в футере. Сейчас его нет/плохо видно.",
    ),
    "has_price": (
        "Покажи цены на страницах услуг",
        "Укажи «от XXXX ₽» рядом с каждой услугой. Конкуренты показывают — "
        "значит клиенты ожидают это увидеть.",
    ),
}

# Schema types worth nudging for in tourism/active leisure. Mapping
# type → (short label, why it matters).
HIGH_VALUE_SCHEMA: dict[str, tuple[str, str]] = {
    "AggregateRating": (
        "Schema AggregateRating",
        "Позволяет выводить звёзды рейтинга в выдаче Яндекса. "
        "Увеличивает CTR.",
    ),
    "Product": (
        "Schema Product",
        "Описывает тур/услугу как продукт — Яндекс понимает цены, "
        "наличие, категорию.",
    ),
    "Service": (
        "Schema Service",
        "Структурированное описание услуги — помогает SERP-сниппетам.",
    ),
    "TouristTrip": (
        "Schema TouristTrip",
        "Специализированный туристический Schema — прямое попадание "
        "в туристические блоки Яндекса.",
    ),
    "FAQPage": (
        "Schema FAQPage",
        "Раскрывает FAQ прямо в выдаче, забирая больше real-estate.",
    ),
    "Organization": (
        "Schema Organization",
        "Базовый идентификационный блок. Без него сайту сложнее "
        "получать Knowledge Graph.",
    ),
}


# Filler tokens dropped from query grouping — short connectors that
# don't change the topical meaning.
STOP_TOKENS: frozenset[str] = frozenset({
    "в", "во", "на", "у", "по", "из", "от", "до", "за", "для",
    "и", "а", "или", "но", "с", "со", "о", "об",
    "цена", "цены", "стоимость", "туры", "тур", "под", "ключ",
    "забронировать", "купить", "заказать", "заказ", "выбрать",
    "недорого", "дёшево", "недорогой",
    "2025", "2026", "2027",
})


def _norm_query(q: str) -> str:
    q = q.lower()
    q = re.sub(r"[^a-zа-яё0-9\s-]", " ", q)
    tokens = [t for t in q.split() if t and t not in STOP_TOKENS and len(t) >= 3]
    return " ".join(sorted(tokens)) if tokens else q.strip()


def _cluster_queries(queries: Sequence[str]) -> dict[str, list[str]]:
    """Group queries by their normalised head.

    Returns {normalised_key: [original_queries]}.
    """
    buckets: dict[str, list[str]] = {}
    for q in queries:
        key = _norm_query(q)
        if not key:
            continue
        buckets.setdefault(key, []).append(q)
    return buckets


def _opp_id(kind: str, *parts: str) -> str:
    h = hashlib.sha256()
    h.update(kind.encode())
    for p in parts:
        h.update(b"|")
        h.update((p or "").encode())
    return h.hexdigest()[:16]


@dataclasses.dataclass
class Opportunity:
    id: str
    source: str               # 'content_gap' | 'feature_diff' | 'schema_diff'
    category: str             # 'new_page' | 'on_page_feature' | 'schema' | 'contact'
    priority: str             # 'high' | 'medium' | 'low'
    title_ru: str
    reasoning_ru: str
    suggested_action_ru: str
    evidence: dict

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Source 1: content gaps ────────────────────────────────────────────

def _content_gap_opportunities(gap_rows: Sequence[dict]) -> list[Opportunity]:
    """Group gaps by normalised query, emit one "create page" per group."""
    if not gap_rows:
        return []

    buckets: dict[str, list[dict]] = {}
    for row in gap_rows:
        q = (row or {}).get("query") or ""
        if not q:
            continue
        key = _norm_query(q)
        buckets.setdefault(key, []).append(row)

    out: list[Opportunity] = []
    for key, rows in buckets.items():
        rows = sorted(rows, key=lambda r: r.get("competitor_position", 99))
        top = rows[0]
        queries = [r.get("query") for r in rows if r.get("query")]
        best_comp_pos = top.get("competitor_position", 99)
        site_pos = top.get("site_position")

        # Priority: top-3 competitor position + site absent = high
        if best_comp_pos <= 3 and (site_pos is None or site_pos > 30):
            prio = "high"
        elif best_comp_pos <= 5:
            prio = "medium"
        else:
            prio = "low"

        # Human title uses the shortest canonical-looking query in the cluster
        display_query = min(queries, key=len) if queries else key
        title = f"Создай страницу по теме «{display_query}»"
        reasoning = (
            f"Конкурент {top.get('competitor_domain')} стоит на позиции "
            f"{best_comp_pos} по этому запросу, ты "
            + (f"на позиции {site_pos}" if site_pos else "не ранжируешься в топ-100")
            + ". В кластере "
            + f"{len(queries)} похожих запрос"
            + ("" if len(queries) == 1 else ("а" if 1 < len(queries) < 5 else "ов"))
            + "."
        )
        action = (
            "Сделай отдельную посадочную под эту тему: "
            "заголовок с ключевой фразой, описание услуги, цена, "
            "кнопка брони, 3–5 фото, секция FAQ."
        )
        out.append(Opportunity(
            id=_opp_id("content_gap", key),
            source="content_gap",
            category="new_page",
            priority=prio,
            title_ru=title,
            reasoning_ru=reasoning,
            suggested_action_ru=action,
            evidence={
                "queries": queries[:10],
                "competitor_domain": top.get("competitor_domain"),
                "competitor_position": best_comp_pos,
                "competitor_url": top.get("competitor_url"),
                "competitor_title": top.get("competitor_title"),
                "site_position": site_pos,
                "other_competitors": top.get("other_competitors") or [],
            },
        ))

    out.sort(key=lambda o: {"high": 0, "medium": 1, "low": 2}[o.priority])
    return out


# ── Source 2: feature diff ────────────────────────────────────────────

def _feature_diff_opportunities(
    own: dict, competitors: Sequence[dict],
) -> list[Opportunity]:
    if not competitors:
        return []

    total = len(competitors)
    threshold = max(1, total // 2)  # at least half have it

    out: list[Opportunity] = []
    for feat, (short, full) in FEATURE_LABELS.items():
        if own.get(feat):
            continue
        have = [c for c in competitors if c.get(feat)]
        if len(have) < threshold:
            continue
        # Priority: missing and >=80% competitors have → high
        share = len(have) / total
        if share >= 0.8:
            prio = "high"
        elif share >= 0.5:
            prio = "medium"
        else:
            prio = "low"
        competitors_with = [c.get("domain") for c in have if c.get("domain")]
        out.append(Opportunity(
            id=_opp_id("feature_diff", feat),
            source="feature_diff",
            category="contact" if feat in ("has_phone", "has_whatsapp", "has_telegram") else "on_page_feature",
            priority=prio,
            title_ru=short,
            reasoning_ru=(
                f"{len(have)} из {total} найденных конкурентов имеют этот "
                f"элемент. У тебя — нет."
            ),
            suggested_action_ru=full,
            evidence={
                "feature": feat,
                "competitors_with": competitors_with,
                "competitors_without": [
                    c.get("domain") for c in competitors
                    if not c.get(feat) and c.get("domain")
                ],
                "share_competitors_with": round(share, 2),
            },
        ))
    out.sort(key=lambda o: {"high": 0, "medium": 1, "low": 2}[o.priority])
    return out


# ── Source 3: schema diff ─────────────────────────────────────────────

def _schema_diff_opportunities(
    own: dict, competitors: Sequence[dict],
) -> list[Opportunity]:
    if not competitors:
        return []
    own_types = set(own.get("schema_types") or [])

    # Count how many competitors have each high-value type
    type_counts: Counter[str] = Counter()
    for c in competitors:
        for t in c.get("schema_types") or []:
            if t in HIGH_VALUE_SCHEMA:
                type_counts[t] += 1

    out: list[Opportunity] = []
    for schema_type, count in type_counts.most_common():
        if schema_type in own_types:
            continue
        label, why = HIGH_VALUE_SCHEMA[schema_type]
        competitors_with = [
            c.get("domain") for c in competitors
            if schema_type in (c.get("schema_types") or []) and c.get("domain")
        ]
        # Higher priority for AggregateRating since it directly affects CTR
        if schema_type == "AggregateRating":
            prio = "high"
        elif count >= max(2, len(competitors) // 2):
            prio = "medium"
        else:
            prio = "low"
        out.append(Opportunity(
            id=_opp_id("schema_diff", schema_type),
            source="schema_diff",
            category="schema",
            priority=prio,
            title_ru=f"Добавь {label}",
            reasoning_ru=(
                f"{count} из {len(competitors)} конкурентов используют эту "
                f"разметку. У тебя её нет."
            ),
            suggested_action_ru=why,
            evidence={
                "schema_type": schema_type,
                "competitors_with": competitors_with,
            },
        ))
    return out


# ── Orchestrator ──────────────────────────────────────────────────────

def build_growth_opportunities(
    *,
    content_gaps: Sequence[dict] | None,
    deep_dive_self: dict | None,
    deep_dive_competitors: Sequence[dict] | None,
    max_items: int = 15,
) -> list[dict]:
    """Compose all opportunity sources into a prioritized JSONB-ready list."""
    gaps = list(content_gaps or [])
    own = dict(deep_dive_self or {})
    competitors = list(deep_dive_competitors or [])

    items: list[Opportunity] = []
    items += _content_gap_opportunities(gaps)
    items += _feature_diff_opportunities(own, competitors)
    items += _schema_diff_opportunities(own, competitors)

    # Final ordering: high first; within a tier keep the insertion order
    # (content_gap first, then feature_diff, then schema_diff) so the
    # user sees the biggest-money actions on top.
    items.sort(key=lambda o: {"high": 0, "medium": 1, "low": 2}[o.priority])
    return [o.to_dict() for o in items[:max_items]]


__all__ = [
    "Opportunity",
    "build_growth_opportunities",
    "FEATURE_LABELS",
    "HIGH_VALUE_SCHEMA",
]
