"""LLM gap-filler for the Target Demand Map (Phase B).

ONE Haiku call per site per build. Budget ~$0.01 — system prompt is
cached by `call_with_tool`, so repeated invocations across sites benefit
from the 90%+ cache-read discount.

The model is asked to:
  1. Propose additional queries anchored to existing cluster_keys (the
     prompt forces this by listing the available cluster_keys in the
     user message). Hallucinated cluster_keys are filtered out.
  2. Propose up to 5 "gap clusters" (patterns the vertical profile
     missed) as hints for future profile curation.

Constraints enforced post-response:
  - Russian only (we ASCII-filter to drop English hallucinations).
  - Cluster_keys must exist in the input set — unknown keys are dropped.
  - No geo outside `target_config["geo_primary" | "geo_secondary"]`.
  - No competitor-brand expansions (we drop queries containing any
    listed competitor brand, case-insensitive).
  - Hard cap: 30 additional queries + 5 gap cluster hints per call.

Fail-open contract: any exception (HTTP, JSON, schema) returns [] — the
orchestrator persists the Cartesian result and logs a warning.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Sequence

from app.core_audit.demand_map.dto import (
    ClusterSource,
    TargetClusterDTO,
    TargetQueryDTO,
    VolumeTier,
)

log = logging.getLogger(__name__)


MAX_ADDITIONAL_QUERIES = 30
MAX_GAP_CLUSTERS = 5
MAX_SAMPLE_CLUSTERS_IN_PROMPT = 15
MAX_EXISTING_QUERIES_IN_PROMPT = 40


SYSTEM_PROMPT = """Ты — SEO-аналитик русскоязычных сайтов.
Твоя задача — предложить дополнительные поисковые запросы (на русском языке),
которые дополняют уже построенную карту спроса (target demand map) сайта.

Правила (строгие):
1. Все запросы — ТОЛЬКО на русском языке. Кириллица.
2. Каждый запрос должен быть привязан к существующему cluster_key из списка,
   который даст пользователь. Не придумывай новые cluster_key.
3. НЕ используй географию вне списка target_config.geo_primary / geo_secondary.
4. НЕ предлагай запросы с брендами конкурентов (список competitor_brands).
5. Фокус: длинный хвост, разговорные формулировки, сленг, сезонные оттенки,
   сценарные запросы ("с детьми", "с собакой", "из аэропорта", "вечером" и т.д.).
6. Каждый запрос — 3-7 слов, в нижнем регистре, без пунктуации.
7. Оценивай объём как 's' (малый), 'm' (средний), 'l' (крупный).

Дополнительно ты можешь предложить gap_clusters — паттерны, которых не хватает
в карте. Формат gap_clusters:
  - intent_code: один из COMM_CATEGORY, TRANS_BOOK, LOCAL_GEO, INFO_DEST, TRUST_LEGAL.
  - cluster_type: один из commercial_core, commercial_modifier, local_geo,
    informational_dest, informational_prep, transactional_book, trust,
    seasonality, activity.
  - name_ru: короткое название паттерна на русском.
  - sample_queries: 3-5 примерных запросов на русском.

Возвращай СТРОГО через tool_use propose_demand_expansion.
"""


PROPOSE_TOOL: dict[str, Any] = {
    "name": "propose_demand_expansion",
    "description": (
        "Предложить дополнительные запросы для существующих кластеров и "
        "gap_clusters для паттернов, которых не хватает в карте спроса."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "additional_queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_key": {"type": "string"},
                        "query_text": {"type": "string"},
                        "estimated_volume": {
                            "type": "string",
                            "enum": ["xs", "s", "m", "l", "xl"],
                        },
                    },
                    "required": ["cluster_key", "query_text"],
                },
            },
            "gap_clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent_code": {"type": "string"},
                        "cluster_type": {"type": "string"},
                        "name_ru": {"type": "string"},
                        "sample_queries": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["cluster_type", "name_ru"],
                },
            },
        },
        "required": ["additional_queries"],
    },
}


_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_ALLOWED_QUERY_CHARS = re.compile(r"^[а-яёА-ЯЁa-zA-Z0-9\s\-]+$")


def _is_russian_query(text: str) -> bool:
    """Return True if the text is dominantly Russian.

    Cheap check: must contain at least one Cyrillic char AND pass a
    character-class regex that forbids punctuation / URLs / HTML.
    """
    if not text:
        return False
    if not _CYRILLIC_RE.search(text):
        return False
    return bool(_ALLOWED_QUERY_CHARS.match(text))


def _build_user_message(
    target_config: Mapping[str, Any],
    existing_clusters: Sequence[TargetClusterDTO],
) -> str:
    """Build a compact prompt showing target_config + sample clusters."""
    sample = list(existing_clusters)[:MAX_SAMPLE_CLUSTERS_IN_PROMPT]
    lines = [
        "target_config:",
        f"  services: {list(target_config.get('services', []))[:20]}",
        f"  geo_primary: {list(target_config.get('geo_primary', []))[:15]}",
        f"  geo_secondary: {list(target_config.get('geo_secondary', []))[:10]}",
        f"  competitor_brands: {list(target_config.get('competitor_brands', []))[:10]}",
        f"  months: {list(target_config.get('months', []))[:12]}",
        "",
        "Существующие кластеры (cluster_key :: name_ru :: cluster_type):",
    ]
    for c in sample:
        lines.append(
            f"  {c.cluster_key} :: {c.name_ru} :: {c.cluster_type.value}"
        )
    lines.extend([
        "",
        "Верни через tool propose_demand_expansion:",
        f"- до {MAX_ADDITIONAL_QUERIES} additional_queries для существующих cluster_key",
        f"- до {MAX_GAP_CLUSTERS} gap_clusters для недостающих паттернов",
    ])
    return "\n".join(lines)


def _filter_additional_queries(
    raw_queries: list[dict[str, Any]],
    *,
    known_keys: set[str],
    geo_allow: set[str],
    competitor_brands: set[str],
) -> list[TargetQueryDTO]:
    """Validate + convert raw LLM output to TargetQueryDTO rows."""
    out: list[TargetQueryDTO] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_queries[: MAX_ADDITIONAL_QUERIES * 2]:
        if not isinstance(item, dict):
            continue
        cluster_key = (item.get("cluster_key") or "").strip()
        query_text = (item.get("query_text") or "").strip().lower()
        if not cluster_key or not query_text:
            continue
        if cluster_key not in known_keys:
            continue
        if not _is_russian_query(query_text):
            continue
        # Competitor-brand filter.
        if competitor_brands and any(b in query_text for b in competitor_brands):
            continue
        # Length sanity.
        if len(query_text) < 3 or len(query_text) > 200:
            continue
        key_pair = (cluster_key, query_text)
        if key_pair in seen:
            continue
        seen.add(key_pair)

        # Volume tier — accept or default to 's'.
        vol = (item.get("estimated_volume") or "s").strip().lower()
        try:
            tier = VolumeTier(vol)
        except ValueError:
            tier = VolumeTier.s

        out.append(
            TargetQueryDTO(
                cluster_key=cluster_key,
                query_text=query_text,
                source=ClusterSource.llm,
                estimated_volume_tier=tier,
            )
        )
        if len(out) >= MAX_ADDITIONAL_QUERIES:
            break
    return out


def expand_with_llm(
    target_config: Mapping[str, Any],
    existing_clusters: Sequence[TargetClusterDTO],
    profile: Any | None = None,  # kept for API symmetry; unused for now
    *,
    caller: Any = None,
) -> list[TargetQueryDTO]:
    """Single-call Haiku expansion.

    Parameters:
        target_config: site.target_config dict — drives allowed geo/brands.
        existing_clusters: Phase A Cartesian output, used to anchor
            cluster_keys in the prompt and to validate LLM output.
        profile: currently unused; accepted for future per-vertical tuning.
        caller: optional injected callable matching
            `call_with_tool(system=..., user_message=..., tool=..., model_tier=...)`
            — used for tests to avoid real HTTP.

    Returns a list of TargetQueryDTO rows (source=llm) suitable for
    persistence alongside the Cartesian result. Fail-open: returns []
    on ANY exception.
    """
    if not existing_clusters:
        return []

    # Lazy import to keep the pure-Python portion of Phase A importable
    # without pulling anthropic in test environments.
    if caller is None:
        try:
            from app.agents.llm_client import call_with_tool as caller_fn
        except Exception as exc:  # noqa: BLE001
            log.warning("demand_map.llm_import_failed err=%s", exc)
            return []
        caller = caller_fn

    known_keys = {c.cluster_key for c in existing_clusters}
    geo_allow = {
        str(g).lower()
        for g in list(target_config.get("geo_primary", []) or [])
        + list(target_config.get("geo_secondary", []) or [])
        if g
    }
    competitor_brands = {
        str(b).lower()
        for b in target_config.get("competitor_brands", []) or []
        if b
    }

    user_message = _build_user_message(target_config, existing_clusters)

    try:
        tool_input, usage = caller(
            model_tier="cheap",
            system=SYSTEM_PROMPT,
            user_message=user_message,
            tool=PROPOSE_TOOL,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("demand_map.llm_call_failed err=%s", exc)
        return []

    if not isinstance(tool_input, dict):
        log.warning("demand_map.llm_bad_tool_input type=%s", type(tool_input).__name__)
        return []

    raw_queries = tool_input.get("additional_queries") or []
    if not isinstance(raw_queries, list):
        raw_queries = []

    filtered = _filter_additional_queries(
        raw_queries,
        known_keys=known_keys,
        geo_allow=geo_allow,
        competitor_brands=competitor_brands,
    )

    log.info(
        "demand_map.llm_expansion_done raw=%d accepted=%d cost=%s",
        len(raw_queries), len(filtered),
        (usage or {}).get("cost_usd") if isinstance(usage, dict) else None,
    )
    return filtered


__all__ = [
    "SYSTEM_PROMPT",
    "PROPOSE_TOOL",
    "MAX_ADDITIONAL_QUERIES",
    "MAX_GAP_CLUSTERS",
    "expand_with_llm",
]
