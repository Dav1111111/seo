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
import re
import urllib.error
import urllib.request
from typing import Any, Callable
from xml.etree import ElementTree as ET

from app.collectors.yandex_serp import check_indexation, fetch_serp, _normalise_host
from app.core_audit.competitors.discovery import EXCLUDED_DOMAIN_SUFFIXES


# Threshold for "suspiciously low indexation" — below this we launch
# diagnostic steps instead of calling the scenario done. A brand-new
# site can genuinely have 1-2 pages and that's fine; but the owner
# still benefits from the diagnostic being offered, so we treat <3
# as the cut-off. Tune if we start seeing false positives.
LOW_INDEX_THRESHOLD = 3

# User-Agent Yandex actually sends. Using it on fetches reveals what
# the bot sees — e.g. a React SPA that serves empty `<div id="root">`
# to bots but full content to real browsers.
YANDEX_BOT_UA = "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"

HTTP_TIMEOUT_SEC = 10.0


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

    `human_summary_ru` is the plain-Russian interpretation of the data
    — what it means, is it good or bad, why it matters. Rendered
    prominently in the UI *above* the raw JSON so the owner doesn't
    have to read field names to understand the result. Optional —
    simple steps that just show data may leave it None.
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
    human_summary_ru: str | None = None
    # Marks the severity of human_summary_ru so the UI can colour
    # the callout: "info" | "good" | "warning" | "bad". Green for
    # "это в порядке", amber for soft issues, red for blockers.
    human_summary_level: str = "info"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Scenario 1: indexation check ──────────────────────────────────────

INDEXATION_META = ScenarioMeta(
    id="indexation",
    title_ru="Проверить индексацию сайта",
    description_ru=(
        "Дёргаем Yandex Search API с запросом site:домен. "
        "Если страниц в индексе подозрительно мало, можно продолжить — "
        "сценарий сам найдёт причину: sitemap, robots.txt, рендеринг под "
        "YandexBot, и даст конкретное действие."
    ),
    inputs=[
        ScenarioInput(
            key="domain",
            label_ru="Домен сайта",
            placeholder_ru="grandtourspirit.ru",
        ),
    ],
    step_count=4,   # up to 4 if diagnostic continues; 1 if not
)


# ── Diagnostic fetchers ───────────────────────────────────────────────
# Small stdlib-only helpers so each step remains easy to read and debug.
# Every failure mode returns a dataclass-ish dict the step function
# renders — no exceptions bubble to the API layer.

def _http_get(url: str, *, user_agent: str | None = None) -> dict:
    """Generic GET that never raises.

    Returns {status, body, content_type, error}. `body` capped at
    200 KB so a surprise large page doesn't blow out memory.
    """
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": user_agent or YANDEX_BOT_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read(200_000).decode("utf-8", errors="replace")
            return {
                "status": resp.getcode(),
                "body": raw,
                "content_type": resp.headers.get("Content-Type", ""),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "body": "",
            "content_type": "",
            "error": f"http_{exc.code}",
        }
    except urllib.error.URLError as exc:
        return {
            "status": 0,
            "body": "",
            "content_type": "",
            "error": f"network: {exc.reason}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": 0,
            "body": "",
            "content_type": "",
            "error": f"exception: {type(exc).__name__}",
        }


def _inspect_sitemap(domain: str) -> dict:
    """Pull /sitemap.xml, count <loc> entries, sample the first few.

    The sitemap being HTML (i.e. `<!DOCTYPE html>...`) is a classic
    SPA symptom where the client-side router swallows static routes —
    we flag this explicitly because it's the single most fixable
    cause of "Yandex only sees my homepage".
    """
    url = f"https://{domain}/sitemap.xml"
    res = _http_get(url)
    if res["error"]:
        return {"url": url, "status": res["status"], "error": res["error"]}

    body = res["body"].strip()
    content_type = res["content_type"].lower()
    looks_html = body[:200].lower().lstrip().startswith(("<!doctype html", "<html"))

    if looks_html or "text/html" in content_type:
        return {
            "url": url,
            "status": res["status"],
            "valid_xml": False,
            "problem": "sitemap_returns_html",
            "hint_ru": (
                "Вместо XML сайт отдал HTML — скорее всего SPA-роутер "
                "перехватывает путь. Яндекс не видит список страниц."
            ),
        }

    urls_in_sitemap: list[str] = []
    try:
        root = ET.fromstring(body)
        # Strip the XML namespace to make find() simpler
        for loc in root.iter():
            if loc.tag.endswith("}loc") or loc.tag == "loc":
                if loc.text:
                    urls_in_sitemap.append(loc.text.strip())
    except ET.ParseError as exc:
        return {
            "url": url,
            "status": res["status"],
            "valid_xml": False,
            "problem": "sitemap_xml_parse_error",
            "parse_error": str(exc)[:120],
        }

    return {
        "url": url,
        "status": res["status"],
        "valid_xml": True,
        "urls_declared": len(urls_in_sitemap),
        "sample": urls_in_sitemap[:10],
    }


def _inspect_robots(domain: str) -> dict:
    """Fetch /robots.txt and call out bad patterns.

    Worst case: a SPA returns the root HTML here too, which Yandex
    interprets as "no rules" and may also treat as "weirdly misbehaving
    server". We also flag any blanket Disallow on / since that would
    block Yandex outright.
    """
    url = f"https://{domain}/robots.txt"
    res = _http_get(url)
    if res["error"]:
        return {"url": url, "status": res["status"], "error": res["error"]}

    body = res["body"]
    content_type = res["content_type"].lower()

    is_html = body[:200].lower().lstrip().startswith(("<!doctype html", "<html"))
    if is_html or "text/html" in content_type:
        return {
            "url": url,
            "status": res["status"],
            "problem": "robots_returns_html",
            "hint_ru": "SPA отдаёт HTML вместо robots.txt — Яндекс не знает правил обхода.",
        }

    # Find Yandex-relevant directives
    blocks_all = False
    sitemap_line = None
    disallow_lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            disallow_lines.append(path)
            if path == "/":
                blocks_all = True
        elif lower.startswith("sitemap:"):
            sitemap_line = line.split(":", 1)[1].strip()

    return {
        "url": url,
        "status": res["status"],
        "problem": "blocks_all_root_path" if blocks_all else None,
        "disallow_count": len(disallow_lines),
        "disallow_sample": disallow_lines[:5],
        "sitemap_referenced": bool(sitemap_line),
        "sitemap_url": sitemap_line,
    }


def _inspect_homepage_rendering(domain: str) -> dict:
    """Fetch the homepage as YandexBot and see if real content is there.

    A React SPA without SSR serves `<div id="root"></div>` and all text
    gets injected by JavaScript. Yandex's crawler runs JS but not
    reliably — if the pre-JS body is empty, indexation suffers. We
    check: <title> present, some visible text after stripping tags,
    and the presence of a lone react root div.
    """
    url = f"https://{domain}/"
    res = _http_get(url)
    if res["error"]:
        return {"url": url, "status": res["status"], "error": res["error"]}

    html = res["body"]
    # Crude but sufficient for diagnostic: title + body text length
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    # Text visible to the bot = html minus all tags & scripts/styles
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    text_len = len(stripped)

    # SPA shell detection
    spa_root_alone = bool(
        re.search(
            r'<body[^>]*>\s*<div id="root"[^>]*>\s*</div>\s*</body>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
    )

    # Classify
    if res["status"] != 200:
        problem = f"http_{res['status']}"
    elif spa_root_alone:
        problem = "empty_spa_shell"
    elif text_len < 200:
        problem = "almost_no_text_in_html"
    elif not title:
        problem = "missing_title"
    else:
        problem = None

    return {
        "url": url,
        "status": res["status"],
        "title": title[:120],
        "text_length": text_len,
        "spa_root_only": spa_root_alone,
        "problem": problem,
    }


def _explain_serp(domain: str, pages_found: int, error: str | None) -> tuple[str, str]:
    """Human explanation of step 1 — what the site: query told us."""
    if error:
        return (
            f"Search API вернул ошибку: {error}. Не получилось спросить Яндекс.",
            "bad",
        )
    if pages_found == 0:
        return (
            f"По запросу site:{domain} Яндекс не нашёл ни одной страницы. "
            "Сайт фактически невидим в поиске — владелец сейчас не получает "
            "органический трафик из Яндекса вообще.",
            "bad",
        )
    if pages_found < LOW_INDEX_THRESHOLD:
        return (
            f"В индексе всего {pages_found} страниц. У живого сайта обычно "
            "хотя бы 5-10 — это мало. Ниже пошагово разберёмся почему.",
            "warning",
        )
    return (
        f"В индексе {pages_found} страниц. Для обычного сайта это нормальный "
        "уровень — Яндекс видит ресурс и показывает его в поиске.",
        "good",
    )


def _explain_sitemap(info: dict) -> tuple[str, str]:
    if info.get("error"):
        return (
            f"Не удалось получить sitemap: {info['error']}. Значит либо файла "
            "нет по стандартному пути, либо сервер не отвечает. Яндекс тоже не "
            "может его забрать — это почти всегда проблема для индексации.",
            "bad",
        )
    if info.get("problem") == "sitemap_returns_html":
        return (
            "Вместо XML по пути /sitemap.xml сайт отдаёт HTML-страницу. Это "
            "значит, что SPA-роутер (React / Vue / и т.п.) перехватывает этот "
            "путь и возвращает главную вместо настоящего файла. Яндекс не может "
            "прочитать список страниц — и поэтому не знает, что ещё индексировать.",
            "bad",
        )
    if info.get("problem") == "sitemap_xml_parse_error":
        return (
            f"Sitemap отдаётся, но XML невалиден: {info.get('parse_error')}. "
            "Яндекс отклоняет такие файлы — фактически сайт без sitemap.",
            "bad",
        )
    declared = info.get("urls_declared", 0)
    if declared == 0:
        return (
            "Sitemap валидный по формату, но в нём 0 URL. Пустой sitemap = его "
            "нет с точки зрения Яндекса. Нужно заполнить его реальными URL сайта.",
            "bad",
        )
    if declared < 5:
        return (
            f"Sitemap валидный, но в нём всего {declared} URL. Маловато — "
            "если на сайте больше страниц, они просто не попадают в sitemap "
            "и, как следствие, не приоритетны для обхода Яндексом.",
            "warning",
        )
    return (
        f"Sitemap валидный, заявлено {declared} URL. Яндекс видит список страниц "
        "и знает, куда идти. Это правильно.",
        "good",
    )


def _explain_robots(info: dict) -> tuple[str, str]:
    if info.get("error"):
        return (
            f"Не удалось получить robots.txt: {info['error']}. Это не критично "
            "(по умолчанию Яндекс индексирует всё), но странно для продакшена.",
            "warning",
        )
    if info.get("problem") == "blocks_all_root_path":
        return (
            "В robots.txt стоит `Disallow: /` — это запрещает Яндексу обходить "
            "ВЕСЬ сайт. Критическая ошибка: пока это правило действует, "
            "никакой индексации не будет, сколько бы sitemap'ов ты ни пинговал.",
            "bad",
        )
    if info.get("problem") == "robots_returns_html":
        return (
            "По пути /robots.txt сайт отдаёт HTML-страницу вместо текстового "
            "файла. Тот же симптом SPA-роутера, что с sitemap: Яндекс получает "
            "не правила обхода, а главную страницу. По факту сайт для Яндекса "
            "без robots.txt — это не блокирует индексацию, но ломает ему картину.",
            "warning",
        )
    disallow = info.get("disallow_count", 0)
    sitemap_ref = info.get("sitemap_referenced", False)
    parts = [f"robots.txt в порядке. Отключающих правил: {disallow}"]
    if sitemap_ref:
        parts.append("ссылка на sitemap указана — Яндекс найдёт карту быстрее")
    else:
        parts.append("ссылка на sitemap не указана (не критично, но хорошо бы добавить)")
    return (". ".join(parts) + ".", "good" if sitemap_ref else "warning")


def _explain_rendering(info: dict) -> tuple[str, str]:
    if info.get("error"):
        return (
            f"Главная не отвечает: {info['error']}. Пока это не починится, "
            "Яндекс не сможет её проиндексировать.",
            "bad",
        )
    status = info.get("status", 0)
    if status and status >= 400:
        return (
            f"Главная возвращает HTTP {status} вместо 200. Для Яндекса это "
            "значит «страница битая» — индексации не будет, пока не починишь сервер.",
            "bad",
        )
    problem = info.get("problem")
    if problem == "empty_spa_shell":
        return (
            "YandexBot видит только пустой React-каркас: `<div id=\"root\"></div>` "
            "и всё. Контент появляется после выполнения JavaScript, а Яндекс "
            "рендерит JS очень нестабильно. Это классическая причина слабой "
            "индексации SPA-сайтов — его нужно либо SSR'ить (Next.js), либо "
            "прокидывать важные мета-теги и текст через nginx до JS.",
            "bad",
        )
    if problem == "almost_no_text_in_html":
        return (
            f"На главной всего {info.get('text_length', 0)} символов видимого "
            "текста в HTML (без тегов). Этого мало — Яндексу буквально нечего "
            "индексировать. Либо контент рендерится JS, либо страница действительно "
            "пустая.",
            "bad",
        )
    if problem == "missing_title":
        return (
            "На главной нет тега <title>. Яндекс не знает, как назвать страницу "
            "в выдаче — она может попасть в индекс, но будет выглядеть плохо.",
            "warning",
        )
    title = info.get("title", "")
    text_len = info.get("text_length", 0)
    return (
        f"YandexBot видит title «{title}» и {text_len} символов текста в HTML. "
        "Всё в порядке — бот получает реальный контент, а не пустой каркас.",
        "good",
    )


def _synthesise_diagnosis(
    pages_found: int,
    sitemap_info: dict,
    robots_info: dict,
    homepage_info: dict,
) -> dict:
    """Rule-based classifier — picks ONE primary cause + next action.

    Order of checks matters: we report the most likely single fix first
    so the owner has a clear path. If everything downstream is fine and
    only the count is low, we attribute to "Yandex slow to crawl, use
    IndexNow" — our built-in solution.
    """
    # 1. Catastrophic technical problems come first
    if homepage_info.get("status") and homepage_info["status"] >= 400:
        return {
            "verdict": "главная страница не отвечает",
            "cause_ru": (
                f"Главная возвращает HTTP {homepage_info['status']}. "
                "Яндекс не может её проиндексировать, а без главной "
                "не индексируются и остальные страницы."
            ),
            "action_ru": "Проверь сервер / деплой / nginx — сайт должен отвечать 200 на /.",
            "severity": "critical",
        }

    if robots_info.get("problem") == "blocks_all_root_path":
        return {
            "verdict": "robots.txt блокирует весь сайт",
            "cause_ru": "В robots.txt стоит `Disallow: /` — Яндексу запрещён обход всего сайта.",
            "action_ru": "Убери `Disallow: /` из robots.txt (оставь запрет только на /admin, /login и т.п.).",
            "severity": "critical",
        }

    if robots_info.get("problem") == "robots_returns_html":
        return {
            "verdict": "SPA перехватывает robots.txt",
            "cause_ru": (
                "Вместо robots.txt сайт отдаёт HTML-страницу — это симптом "
                "SPA, которое заворачивает все пути на React-роутер. Яндекс "
                "не может прочитать правила обхода."
            ),
            "action_ru": (
                "Добавь в nginx правило location ~* \\.txt$ или "
                "location = /robots.txt — чтобы отдавать файл как статику "
                "до того как роутер его перехватит."
            ),
            "severity": "high",
        }

    if sitemap_info.get("problem") == "sitemap_returns_html":
        return {
            "verdict": "SPA перехватывает sitemap.xml",
            "cause_ru": (
                "Сайт отдаёт HTML вместо XML по /sitemap.xml. Яндекс не "
                "видит список страниц сайта, поэтому не знает что индексировать."
            ),
            "action_ru": (
                "В nginx добавь location = /sitemap.xml отдачу статического файла "
                "до SPA-роутера."
            ),
            "severity": "high",
        }

    if homepage_info.get("problem") == "empty_spa_shell":
        return {
            "verdict": "пустой React-каркас для бота",
            "cause_ru": (
                "Главная отдаёт Яндекс-боту только <div id=\"root\"></div> — "
                "контент появляется через JavaScript. Яндекс рендерит JS "
                "нестабильно, поэтому индексирует плохо."
            ),
            "action_ru": (
                "Нужен SSR (Next.js / Nuxt / Remix) ЛИБО nginx sub_filter на "
                "ключевые мета-теги + видимый текст для каждой страницы. "
                "Это большая работа — если не готов, хотя бы засунь title, "
                "description, h1 и первый абзац в HTML до JS."
            ),
            "severity": "critical",
        }

    if homepage_info.get("problem") == "almost_no_text_in_html":
        return {
            "verdict": "в HTML почти нет текста",
            "cause_ru": (
                f"На главной всего {homepage_info.get('text_length', 0)} символов "
                "текста в HTML. Яндексу нечего индексировать."
            ),
            "action_ru": "Напиши минимум пару абзацев реального текста на главной.",
            "severity": "high",
        }

    # 2. No obvious technical problem — sitemap gap + slow crawl
    declared = sitemap_info.get("urls_declared", 0)
    if declared >= 5 and pages_found < declared // 2:
        gap = declared - pages_found
        return {
            "verdict": f"Яндекс ещё не обошёл {gap} из {declared} страниц",
            "cause_ru": (
                f"Sitemap объявляет {declared} URL, а в индексе — {pages_found}. "
                "Технических проблем не вижу: sitemap валидный, robots.txt в порядке, "
                "HTML содержит реальный текст. Причина скорее всего в том, что "
                "Яндекс медленно доходит до молодых сайтов без внешних ссылок."
            ),
            "action_ru": (
                "Два способа ускорить: (1) открой webmaster.yandex.ru → «Переобход "
                "страниц» → вставь URL из sitemap. (2) Подключи IndexNow на вкладке "
                "«Индексация в Яндексе» на Обзоре — будет автоматически пинговать "
                "Яндекс после каждого краулинга."
            ),
            "severity": "medium",
        }

    if declared == 0 and not sitemap_info.get("valid_xml"):
        return {
            "verdict": "нет валидного sitemap",
            "cause_ru": "Sitemap не найден или невалидный — Яндекс не знает какие страницы обходить.",
            "action_ru": "Сгенерируй sitemap.xml с полным списком страниц и положи в корень.",
            "severity": "high",
        }

    # 3. All green — just a young site
    return {
        "verdict": "возможно нормальный старт",
        "cause_ru": (
            f"Технических проблем не нашёл: главная отдаёт текст, sitemap "
            f"({declared} URL) валиден, robots.txt в порядке. "
            f"В индексе {pages_found} — это может быть просто молодой сайт, "
            "до которого Яндекс ещё не дошёл."
        ),
        "action_ru": (
            "Подожди 2-4 недели и проверь снова. Ускорить можно через IndexNow "
            "(кнопка на Обзоре) или ручной «Переобход» в Вебмастере."
        ),
        "severity": "low",
    }


def run_indexation(step_index: int, inputs: dict, prior: list[dict]) -> StepResult:
    raw = (inputs.get("domain") or "").strip()
    domain = _normalise_host(raw)
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

    # ── Step 0 · site:domain in Yandex ───────────────────────────
    if step_index == 0:
        result = check_indexation(raw, groups=50)
        pages_list = [
            {"position": p.position, "url": p.url, "title": p.title}
            for p in result.pages[:30]
        ]
        low = result.pages_found < LOW_INDEX_THRESHOLD and result.error is None
        summary, level = _explain_serp(result.domain, result.pages_found, result.error)
        return StepResult(
            step_index=0,
            step_title_ru="Шаг 1 · Запрос site:домен в Яндекс",
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
            next_available=low,
            next_hint_ru=(
                f"Всего {result.pages_found} в индексе — это подозрительно мало. "
                "Запустим диагностику — проверим sitemap, robots.txt и рендеринг."
            ) if low else None,
            human_summary_ru=summary,
            human_summary_level=level,
        )

    # ── Step 1 · sitemap.xml ─────────────────────────────────────
    if step_index == 1:
        info = _inspect_sitemap(domain)
        summary, level = _explain_sitemap(info)
        ok = info.get("error") is None and info.get("problem") is None and info.get("valid_xml")
        hint = (
            f"Sitemap валидный, объявлено {info.get('urls_declared', 0)} URL. "
            "Идём дальше — проверим robots.txt."
            if ok
            else "Нашли проблему с sitemap. Всё равно продолжим — посмотрим robots.txt."
        )
        return StepResult(
            step_index=1,
            step_title_ru="Шаг 2 · Проверка sitemap.xml",
            step_description_ru=(
                "Яндекс узнаёт список страниц сайта из sitemap. "
                "Проверяем: отдаётся ли XML-файл и сколько в нём URL."
            ),
            request_shown={
                "endpoint": f"GET https://{domain}/sitemap.xml",
                "body_preview": {"user_agent": YANDEX_BOT_UA},
            },
            response_summary=info,
            ok=True,
            error=None,
            next_available=True,
            next_hint_ru=hint,
            human_summary_ru=summary,
            human_summary_level=level,
        )

    # ── Step 2 · robots.txt ──────────────────────────────────────
    if step_index == 2:
        info = _inspect_robots(domain)
        summary, level = _explain_robots(info)
        ok = info.get("error") is None and info.get("problem") is None
        hint = (
            "robots.txt в порядке. Дальше посмотрим, что видит YandexBot на главной."
            if ok
            else "Проблема с robots.txt. Всё равно покажем рендеринг — для полной картины."
        )
        return StepResult(
            step_index=2,
            step_title_ru="Шаг 3 · Проверка robots.txt",
            step_description_ru=(
                "robots.txt говорит Яндексу что можно обходить, а что нельзя. "
                "Проверяем: отдаётся ли текст и нет ли случайных Disallow."
            ),
            request_shown={
                "endpoint": f"GET https://{domain}/robots.txt",
                "body_preview": {"user_agent": YANDEX_BOT_UA},
            },
            response_summary=info,
            ok=True,
            error=None,
            next_available=True,
            next_hint_ru=hint,
            human_summary_ru=summary,
            human_summary_level=level,
        )

    # ── Step 3 · homepage rendering under YandexBot ─────────────
    if step_index == 3:
        info = _inspect_homepage_rendering(domain)
        summary, level = _explain_rendering(info)
        problem = info.get("problem")
        hint = (
            "Нашли проблему в рендеринге. Идём к финальному диагнозу."
            if problem
            else "Главная выглядит нормально для бота. Финальный диагноз соберём на следующем шаге."
        )
        return StepResult(
            step_index=3,
            step_title_ru="Шаг 4 · Что видит YandexBot на главной",
            step_description_ru=(
                "Открываем главную с User-Agent YandexBot. Смотрим: есть ли "
                "title, сколько реального текста в HTML, не пустой ли SPA-каркас."
            ),
            request_shown={
                "endpoint": f"GET https://{domain}/",
                "body_preview": {"user_agent": YANDEX_BOT_UA},
            },
            response_summary=info,
            ok=True,
            error=None,
            next_available=True,
            next_hint_ru=hint,
            human_summary_ru=summary,
            human_summary_level=level,
        )

    # ── Step 4 · synthesis ──────────────────────────────────────
    if step_index == 4:
        # Read every prior step's response_summary
        def _prior(i: int) -> dict:
            return prior[i].get("response_summary", {}) if i < len(prior) else {}

        serp = _prior(0)
        sitemap_info = _prior(1)
        robots_info = _prior(2)
        homepage_info = _prior(3)

        pages_found = int(serp.get("pages_found", 0))
        diagnosis = _synthesise_diagnosis(
            pages_found=pages_found,
            sitemap_info=sitemap_info,
            robots_info=robots_info,
            homepage_info=homepage_info,
        )

        # Glue it all into one continuous Russian narrative so the
        # owner doesn't have to scroll through JSON — they read one
        # paragraph and know what happened + what to do.
        summary_text = (
            f"ПРИЧИНА. {diagnosis['cause_ru']}\n\n"
            f"ЧТО ДЕЛАТЬ. {diagnosis['action_ru']}"
        )
        severity_level = {
            "critical": "bad",
            "high": "bad",
            "medium": "warning",
            "low": "good",
        }.get(diagnosis["severity"], "info")

        return StepResult(
            step_index=4,
            step_title_ru=f"Диагноз · {diagnosis['verdict']}",
            step_description_ru=(
                "Свели результаты трёх проверок в один вердикт. "
                "Называем одну главную причину и одно конкретное действие — "
                "чтобы не растекаться, а решать."
            ),
            request_shown=None,
            response_summary={
                "verdict": diagnosis["verdict"],
                "severity": diagnosis["severity"],
                "cause_ru": diagnosis["cause_ru"],
                "action_ru": diagnosis["action_ru"],
                "based_on": {
                    "pages_indexed": pages_found,
                    "sitemap_urls": sitemap_info.get("urls_declared"),
                    "homepage_problem": homepage_info.get("problem"),
                    "robots_problem": robots_info.get("problem"),
                },
            },
            ok=True,
            error=None,
            next_available=False,
            next_hint_ru=None,
            human_summary_ru=summary_text,
            human_summary_level=severity_level,
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
    """Thin wrapper so scenario code stays self-documenting; delegates
    to the canonical `_normalise_host` helper so every entrypoint
    treats owner input identically."""
    return _normalise_host(value)


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
        if err:
            summary = f"Search API вернул ошибку: {err}. Не получилось спросить Яндекс."
            level = "bad"
        elif not raw:
            summary = (
                f"По запросу «{query}» Яндекс ничего не вернул. Либо запрос "
                "слишком редкий, либо Search API в данный момент дал пустоту."
            )
            level = "warning"
        else:
            summary = (
                f"Яндекс показал по запросу «{query}» топ-{len(raw)} результатов. "
                "Это сырая выдача — там есть и твои конкуренты, и маркетплейсы, "
                "и агрегаторы. На следующем шаге отделим первых от остальных."
            )
            level = "info"
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
            human_summary_ru=summary,
            human_summary_level=level,
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
        if kept and dropped:
            fsummary = (
                f"Из {len(kept) + len(dropped)} результатов оставили {len(kept)} "
                f"реальных конкурентов, выкинули {len(dropped)} (маркетплейсы, "
                "соцсети, твой собственный сайт). По-настоящему с тобой "
                f"сражаются за этот запрос именно эти {len(kept)} сайтов."
            )
            flevel = "good"
        elif kept:
            fsummary = (
                f"Все {len(kept)} результатов — реальные конкуренты. В выдаче "
                "нет ни маркетплейсов, ни твоего сайта — значит фильтровать "
                "нечего, но и в топе ты тоже не стоишь."
            )
            flevel = "info"
        else:
            fsummary = (
                f"После фильтра не осталось ни одного конкурента — все {len(dropped)} "
                "доменов из выдачи это маркетплейсы / соцсети / агрегаторы. "
                "Значит по этому запросу реальные бизнесы типа твоего в топ-10 "
                "не попадают — либо нужно брать другой запрос, либо реально "
                "конкуренция идёт против площадок, а не живых сайтов."
            )
            flevel = "warning"
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
            human_summary_ru=fsummary,
            human_summary_level=flevel,
        )

    # ── Step 2: final list with positions ──────────────────────────
    if step_index == 2:
        kept = prior[1].get("response_summary", {}).get("kept", []) if len(prior) >= 2 else []
        # For single-query case, position IS the ranking. Just sort.
        sorted_rows = sorted(kept, key=lambda r: r.get("position", 99))
        if sorted_rows:
            top_domains = ", ".join(r.get("domain", "") for r in sorted_rows[:3])
            fsummary = (
                f"Твои реальные конкуренты по «{query}»: {top_domains}"
                + (" и ещё " + str(len(sorted_rows) - 3) if len(sorted_rows) > 3 else "")
                + ". Именно они показываются Яндексу по этому запросу — их страницы "
                "стоит разобрать подробнее, чтобы понять, чем они цепляют выдачу."
            )
            flevel = "info"
        else:
            fsummary = (
                "Финальный список пустой — после фильтра ничего не осталось. "
                "См. комментарий к предыдущему шагу."
            )
            flevel = "warning"
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
            human_summary_ru=fsummary,
            human_summary_level=flevel,
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
