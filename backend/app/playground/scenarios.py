"""API Playground — step-by-step, paused, inspectable runs.

Where `/connectors` answers "is integration X alive?", the Playground
answers "what EXACTLY does integration X do, step by step?". Each
scenario is a sequence of named steps. The frontend runs them one at
a time, shows the owner the raw request + raw response, and waits for
an explicit "Continue" click before advancing.

Contract — a scenario is stateless on the server side:
  - Frontend sends {scenario_id, step_index, inputs, prior}
  - Backend runs step `step_index`, reads `inputs` + `prior` steps'
    outputs as needed, returns the new step payload
  - Frontend appends payload into its `prior` list before the next call

Keeps the protocol simple, avoids session storage, and lets the owner
pause / re-run / jump at will without our backend caring.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from app.collectors.yandex_serp import check_indexation, fetch_serp
from app.core_audit.competitors.discovery import EXCLUDED_DOMAIN_SUFFIXES


# ── Protocol ──────────────────────────────────────────────────────────

@dataclasses.dataclass
class ScenarioInput:
    """One user-supplied field shown in the scenario form (pre-run)."""
    key: str             # "query"
    label_ru: str        # "Поисковый запрос"
    placeholder_ru: str  # "багги абхазия"
    required: bool = True


@dataclasses.dataclass
class ScenarioMeta:
    """Metadata exposed to the frontend listing. Does NOT include the
    step implementation — that lives in `SCENARIO_FUNCS`."""
    id: str
    title_ru: str
    description_ru: str
    inputs: list[ScenarioInput]
    step_count: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title_ru": self.title_ru,
            "description_ru": self.description_ru,
            "inputs": [dataclasses.asdict(i) for i in self.inputs],
            "step_count": self.step_count,
        }


@dataclasses.dataclass
class StepResult:
    """Return value from running one step.

    `request_shown` holds a compact preview of the outbound call so the
    owner can see the exact query we send — not a paraphrase. Similarly
    `response_summary` is the DATA they care about, trimmed so the UI
    doesn't crash on a 50 KB JSON.
    """
    step_index: int
    step_title_ru: str
    step_description_ru: str
    request_shown: dict | None
    response_summary: dict
    ok: bool
    error: str | None
    next_available: bool
    next_hint_ru: str | None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Scenario 1: indexation check ──────────────────────────────────────

INDEXATION_META = ScenarioMeta(
    id="indexation",
    title_ru="Проверить индексацию сайта",
    description_ru=(
        "Дёргаем Yandex Search API с запросом site:домен. "
        "Видим, сколько страниц сайта реально в индексе Яндекса прямо сейчас — "
        "независимо от Вебмастера."
    ),
    inputs=[
        ScenarioInput(
            key="domain",
            label_ru="Домен сайта",
            placeholder_ru="grandtourspirit.ru",
        ),
    ],
    step_count=1,
)


def run_indexation(step_index: int, inputs: dict, prior: list[dict]) -> StepResult:
    domain = (inputs.get("domain") or "").strip()
    if not domain:
        return StepResult(
            step_index=0,
            step_title_ru="Проверка индексации",
            step_description_ru="Ожидаю домен.",
            request_shown=None,
            response_summary={"error": "empty_domain"},
            ok=False,
            error="Укажи домен.",
            next_available=False,
            next_hint_ru=None,
        )

    # Only one step in this scenario
    if step_index != 0:
        return StepResult(
            step_index=step_index,
            step_title_ru="Всё",
            step_description_ru="У этого сценария один шаг.",
            request_shown=None,
            response_summary={},
            ok=True,
            error=None,
            next_available=False,
            next_hint_ru=None,
        )

    result = check_indexation(domain, groups=50)
    pages_list = [
        {"position": p.position, "url": p.url, "title": p.title}
        for p in result.pages[:30]
    ]
    return StepResult(
        step_index=0,
        step_title_ru="Запрос site:домен в Яндекс",
        step_description_ru=(
            f"Спрашиваем Яндекс: покажи всё, что знаешь про {result.domain}. "
            "Ответ — реальное состояние индекса на этот момент."
        ),
        request_shown={
            "endpoint": "POST /v2/web/searchAsync (searchapi.api.cloud.yandex.net)",
            "body_preview": {
                "query": {"queryText": f"site:{result.domain}"},
                "groupsOnPage": 50,
            },
        },
        response_summary={
            "pages_found": result.pages_found,
            "pages": pages_list,
        },
        ok=result.error is None,
        error=result.error,
        next_available=False,
        next_hint_ru=None,
    )


# ── Scenario 2: competitors by one query ──────────────────────────────

COMPETITORS_BY_QUERY_META = ScenarioMeta(
    id="competitors_by_query",
    title_ru="Найти конкурентов по одному запросу",
    description_ru=(
        "Так же, как это делает задача competitor_discovery на полном пайплайне — "
        "только для одного запроса, чтобы увидеть, что происходит внутри."
    ),
    inputs=[
        ScenarioInput(
            key="query",
            label_ru="Поисковый запрос",
            placeholder_ru="багги абхазия",
        ),
        ScenarioInput(
            key="own_domain",
            label_ru="Твой домен (чтобы исключить себя)",
            placeholder_ru="grandtourspirit.ru",
            required=False,
        ),
    ],
    step_count=3,
)


def _normalise_domain(value: str) -> str:
    v = (value or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v.rstrip("/")


def run_competitors_by_query(
    step_index: int, inputs: dict, prior: list[dict],
) -> StepResult:
    query = (inputs.get("query") or "").strip()
    own_domain = _normalise_domain(inputs.get("own_domain") or "")
    if not query:
        return StepResult(
            step_index=0,
            step_title_ru="Нужен запрос",
            step_description_ru="Введи запрос в поле сверху.",
            request_shown=None,
            response_summary={},
            ok=False,
            error="empty_query",
            next_available=False,
            next_hint_ru=None,
        )

    # ── Step 0: fetch SERP ──────────────────────────────────────────
    if step_index == 0:
        docs, err = fetch_serp(query, groups=10)
        raw = [
            {
                "position": d.position,
                "domain": d.domain,
                "url": d.url,
                "title": d.title[:120],
            }
            for d in docs
        ]
        return StepResult(
            step_index=0,
            step_title_ru="Шаг 1/3 · Запрос в Яндекс Поиск",
            step_description_ru=(
                f"Ищем в Яндексе «{query}» — берём топ-10 выдачи. "
                "Это сырой ответ Яндекса, до любой фильтрации."
            ),
            request_shown={
                "endpoint": "POST /v2/web/searchAsync (searchapi.api.cloud.yandex.net)",
                "body_preview": {
                    "query": {"queryText": query, "searchType": "SEARCH_TYPE_RU"},
                    "groupsOnPage": 10,
                    "region": "225 (Россия)",
                },
            },
            response_summary={
                "docs_returned": len(raw),
                "raw_serp": raw,
            },
            ok=err is None,
            error=err,
            next_available=(err is None and len(raw) > 0),
            next_hint_ru="Дальше отфильтруем маркетплейсы и твой собственный домен."
            if err is None and raw
            else None,
        )

    # ── Step 1: filter blacklist + own domain ──────────────────────
    if step_index == 1:
        raw = (
            prior[0].get("response_summary", {}).get("raw_serp", [])
            if prior
            else []
        )
        kept: list[dict] = []
        dropped: list[dict] = []
        for row in raw:
            domain = (row.get("domain") or "").lower()
            if own_domain and (domain == own_domain or domain.endswith("." + own_domain)):
                dropped.append({**row, "reason": "твой сайт"})
                continue
            blacklisted = any(
                domain == s or domain.endswith("." + s)
                for s in EXCLUDED_DOMAIN_SUFFIXES
            )
            if blacklisted:
                dropped.append({**row, "reason": "маркетплейс / соцсеть / агрегатор"})
                continue
            kept.append(row)
        return StepResult(
            step_index=1,
            step_title_ru="Шаг 2/3 · Фильтр — убираем мусор",
            step_description_ru=(
                "Выкидываем твой собственный домен, маркетплейсы "
                "(wildberries, ozon, avito), соцсети (vk, youtube) и агрегаторы "
                "отзывов (2gis, tripadvisor) — они не твои конкуренты, даже если "
                "показываются рядом в выдаче."
            ),
            request_shown=None,   # pure Python filter, no API call
            response_summary={
                "kept_count": len(kept),
                "dropped_count": len(dropped),
                "kept": kept,
                "dropped": dropped,
            },
            ok=True,
            error=None,
            next_available=len(kept) > 0,
            next_hint_ru=f"Осталось {len(kept)} реальных конкурентов. Покажем их с позициями."
            if kept
            else "Все результаты были мусором — это значит по этому запросу все конкуренты — маркетплейсы/агрегаторы. Попробуй другой запрос.",
        )

    # ── Step 2: final list with positions ──────────────────────────
    if step_index == 2:
        kept = prior[1].get("response_summary", {}).get("kept", []) if len(prior) >= 2 else []
        # For single-query case, position IS the ranking. Just sort.
        sorted_rows = sorted(kept, key=lambda r: r.get("position", 99))
        return StepResult(
            step_index=2,
            step_title_ru=f"Шаг 3/3 · Твои конкуренты по запросу «{query}»",
            step_description_ru=(
                "На полном пайплайне (задача competitor_discovery) мы делаем "
                "то же самое, но для 30 запросов сразу, а потом считаем: какой "
                "домен появляется чаще всего. Здесь — для одного запроса, "
                "поэтому порядок = позиция в выдаче."
            ),
            request_shown=None,
            response_summary={
                "competitors_count": len(sorted_rows),
                "competitors": sorted_rows,
            },
            ok=True,
            error=None,
            next_available=False,
            next_hint_ru="Сценарий закончен. Можешь попробовать другой запрос.",
        )

    # Fallback
    return StepResult(
        step_index=step_index,
        step_title_ru="Шаг за пределами сценария",
        step_description_ru="Попробуй начать заново.",
        request_shown=None,
        response_summary={},
        ok=False,
        error="step_out_of_range",
        next_available=False,
        next_hint_ru=None,
    )


# ── Registry ──────────────────────────────────────────────────────────

SCENARIO_META: dict[str, ScenarioMeta] = {
    INDEXATION_META.id: INDEXATION_META,
    COMPETITORS_BY_QUERY_META.id: COMPETITORS_BY_QUERY_META,
}

SCENARIO_FUNCS: dict[str, Callable[[int, dict, list[dict]], StepResult]] = {
    INDEXATION_META.id: run_indexation,
    COMPETITORS_BY_QUERY_META.id: run_competitors_by_query,
}


def list_scenarios() -> list[dict]:
    """Sidebar / landing listing."""
    return [m.to_dict() for m in SCENARIO_META.values()]


def run_step(scenario_id: str, step_index: int, inputs: dict, prior: list[dict]) -> StepResult:
    """Dispatch to the scenario implementation. Guards against unknown
    scenario id / out-of-range step up front so the API returns a clean
    4xx instead of a stack trace."""
    fn = SCENARIO_FUNCS.get(scenario_id)
    if fn is None:
        return StepResult(
            step_index=step_index,
            step_title_ru="Неизвестный сценарий",
            step_description_ru=f"Сценарий «{scenario_id}» не найден.",
            request_shown=None,
            response_summary={},
            ok=False,
            error="unknown_scenario",
            next_available=False,
            next_hint_ru=None,
        )
    return fn(step_index, inputs, prior)


__all__ = [
    "ScenarioInput",
    "ScenarioMeta",
    "StepResult",
    "SCENARIO_META",
    "list_scenarios",
    "run_step",
]
