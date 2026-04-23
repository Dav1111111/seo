"""OnboardingChatAgent — conversational refinement of business understanding.

Replaces the 7-step wizard with a single chat screen. Two entry points:

1. `build_initial_message(understanding)` — takes the one-shot
   `BusinessUnderstandingAgent` output and writes a warm first message
   to the owner proposing services/geos/narrative and asking for
   confirmation. Plain Russian prose, no JSON, no markdown tables.

2. `refine_draft(current_draft, history, latest_user_message)` — takes
   the draft + conversation so far + owner's latest reply; returns a
   short assistant reply + full updated draft + a flag telling the
   caller whether the owner confirmed (and the chat can close).

Design choices:
- Tool_use for the refinement step — reliable structured output.
- Plain text for the initial message — it's a long warm paragraph,
  not fields, so forcing a tool here just adds brittleness.
- Validation layer AFTER the LLM: normalize services (lowercase, split
  multi-word into singles, dedupe, length cap). Defence-in-depth if
  the LLM returns "полноразмерные багги-экспедиции" instead of "багги".
- Round cap at MAX_ROUNDS. Beyond that the caller force-closes the
  chat on the server side.
- Confirmation heuristic: server whitelists obvious "ок, поехали"
  phrases and short-circuits one LLM call.

Cost per round ≈ $0.005 Haiku. Full 15-round conversation ≤ $0.08.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence


log = logging.getLogger(__name__)


MAX_ROUNDS = 15
MAX_SERVICES = 20
MAX_GEO_PRIMARY = 10
MAX_GEO_SECONDARY = 15
MAX_MESSAGE_CHARS = 4000
MAX_NARRATIVE_CHARS = 1200

CONFIRM_REGEX = re.compile(
    r"^\s*(всё\s*ок|все\s*ок|всё\s*верно|все\s*верно|подтверждаю|подтверждено|поехали|"
    r"да,\s*всё\s*так|да,\s*верно|да,\s*ок|готово|согласен|ок,\s*поехали|"
    r"yes|ok)\s*[.!]?\s*$",
    re.IGNORECASE,
)


# ── Initial message ──────────────────────────────────────────────────────

INITIAL_SYSTEM_PROMPT = """\
Вы — дружелюбный консультант по SEO для малого и среднего туристического
бизнеса в России и СНГ. Ваша задача — на основе автоматического анализа
сайта предложить владельцу краткое описание бизнеса, список услуг и
географии, и попросить подтвердить или поправить.

Стиль:
- Обращение только на «вы», тёплое, человеческое, без корпоративного сленга.
- Никаких технических терминов: не говорите «токены», «кластеры», «SEO-ядро»,
  «парсинг». Говорите «услуги», «направления», «география работы».
- Никакого JSON, никакого Markdown, никаких таблиц, никаких списков через
  «-» или «*». Обычный разговорный текст с абзацами.
- Длина: 300–400 слов.
- Язык всегда русский, даже если данные частично на другом языке.
- Уверенный тон: вы делаете предложение, а не задаёте открытые вопросы
  «а что у вас есть?». Предлагайте конкретику — клиент пусть правит.

Структура сообщения:
1. Короткое приветствие и одна-две фразы о том, что вы посмотрели сайт.
2. Ваш проект одного абзаца-описания бизнеса.
3. Перечисление услуг простым языком внутри предложений (не списком).
4. География: что выглядит как основной регион, что как дополнительный.
5. Явный вопрос в конце: «Всё верно, или что-то нужно поправить?» —
   и короткая подсказка, что можно убрать лишнее, добавить недостающее
   или поменять регионы местами.

Важно: услуги и регионы в тексте пишите обычными словами («багги-туры»,
«экспедиции», «Абхазия», «Красная Поляна»), не в нижнем регистре —
нормализация будет на следующем шаге.
"""


def build_initial_user_prompt(
    domain: str,
    display_name: str | None,
    understanding: dict[str, Any],
) -> str:
    narrative = (understanding.get("narrative_ru") or "").strip() or "—"
    niche = (understanding.get("detected_niche") or "").strip() or "—"
    usp = (understanding.get("detected_usp") or "") or "—"
    positioning = (understanding.get("detected_positioning") or "").strip() or "—"

    observed = understanding.get("observed_facts") or []
    observed_text = "\n".join(
        f"- {f.get('fact', '')} ({f.get('page_ref', '')})"
        for f in observed
        if isinstance(f, dict) and f.get("fact")
    ) or "—"

    inferences = understanding.get("inferences") or []
    inferences_text = "\n".join(f"- {x}" for x in inferences if x) or "—"

    uncertainties = understanding.get("uncertainties") or []
    uncertainties_text = "\n".join(f"- {x}" for x in uncertainties if x) or "—"

    return (
        f"Домен: {domain}\n"
        f"Название (если есть): {display_name or '—'}\n\n"
        f"Нарратив, собранный автоматически:\n{narrative}\n\n"
        f"Предполагаемая ниша: {niche}\n"
        f"Предполагаемое УТП: {usp}\n"
        f"Предполагаемое позиционирование: {positioning}\n\n"
        f"Факты, которые мы увидели на сайте:\n{observed_text}\n\n"
        f"Наши догадки (могут быть неточны):\n{inferences_text}\n\n"
        f"Что осталось неясно:\n{uncertainties_text}\n\n"
        "Напишите первое сообщение владельцу по правилам из системного промпта. "
        "В последнем абзаце обязательно задайте вопрос «всё верно?» "
        "и мягко предложите исправить, если что-то не так."
    )


# ── Refinement ───────────────────────────────────────────────────────────

REFINE_SYSTEM_PROMPT = """\
Вы — тот же консультант, который предложил клиенту описание его бизнеса.
Сейчас идёт диалог: клиент правит список услуг, географию или описание.
Ваша задача на каждом шаге — обновить внутреннее представление бизнеса
и коротко ответить клиенту.

Используйте tool `update_business_draft`, чтобы вернуть обновлённое
состояние и ответ клиенту. Никакого текста вне tool_use.

Правила для `services`:
- Только нижний регистр.
- Одно слово или короткая устойчивая фраза (макс. 2 слова): «багги»,
  «экспедиции», «джип-туры», «яхты», «вертолёты», «маршруты», «прокат».
- НЕ используйте длинные фразы вроде «полноразмерные багги-экспедиции
  для VIP» — это не подходит для поиска по тексту страниц.
- При сомнении — дробите: «багги-экспедиции» → «багги», «экспедиции».

Правила для `geo_primary` / `geo_secondary`:
- Нижний регистр. Города и регионы как отдельные элементы:
  «абхазия», «сочи», «адлер», «красная поляна», «крым», «севастополь».
- `geo_primary` — где бизнес реально работает сейчас.
- `geo_secondary` — куда клиент хочет расти, сезонные/эпизодические.

Правила `understanding_patch`:
- Возвращайте ПОЛНОЕ новое состояние, не дифф. Даже поля, которые
  не менялись в этом шаге, присылайте целиком.
- Применяйте последнюю правку клиента; если она противоречит
  предыдущей — выигрывает последняя.
- Если клиент добавил услугу, которой нет на сайте, — принимайте её,
  но в `narrative_ru` мягко отметьте («также выполняете под заказ …»).

Правила `reply_ru`:
- 1–4 коротких предложения, по-русски, на «вы».
- Подтвердите, что поняли правку, перескажите обновлённое состояние
  в 1–2 предложениях прозой, спросите «всё верно теперь?».
- Если клиент сказал «всё ок», «поехали», «подтверждаю», «да, верно» —
  коротко поблагодарите, сообщите, что фиксируете,
  и установите `needs_more_info: false`.
- Если клиент говорит «как скажете, вы эксперт» — не уклоняйтесь,
  уверенно повторите своё предложение и спросите подтверждения.
- Если клиент пишет не по-русски — всё равно отвечайте по-русски.
- Если клиент просит то, что вне вашей задачи (реклама, кластеры,
  правки сайта) — вежливо скажите, что сейчас вы уточняете описание
  бизнеса, и вернитесь к вопросу об услугах/географии/описании.

`needs_more_info`:
- `true` — вы внесли правку, но ждёте подтверждения или уточнения.
- `false` — клиент явно подтвердил финальную версию.
"""


REFINE_TOOL: dict[str, Any] = {
    "name": "update_business_draft",
    "description": (
        "Обновить внутренний черновик описания бизнеса по последней "
        "правке клиента и подготовить короткий ответ."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_ru": {
                "type": "string",
                "description": "Короткий ответ клиенту на русском, 1–4 предложения.",
            },
            "understanding_patch": {
                "type": "object",
                "properties": {
                    "services": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Услуги в нижнем регистре, 1–2 слова каждая.",
                    },
                    "geo_primary": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Основная география в нижнем регистре.",
                    },
                    "geo_secondary": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Дополнительная/сезонная география.",
                    },
                    "narrative_ru": {
                        "type": "string",
                        "description": "Один абзац 2–4 предложения про бизнес.",
                    },
                },
                "required": [
                    "services", "geo_primary", "geo_secondary", "narrative_ru",
                ],
            },
            "needs_more_info": {
                "type": "boolean",
                "description": "true если ждём уточнений, false если клиент подтвердил.",
            },
        },
        "required": ["reply_ru", "understanding_patch", "needs_more_info"],
    },
}


def build_refine_user_prompt(
    current: dict[str, Any],
    history: Sequence[dict[str, str]],
    latest_user_message: str,
) -> str:
    def _fmt_list(xs: Any) -> str:
        return ", ".join(str(x) for x in (xs or [])) or "(пусто)"

    hist_lines: list[str] = []
    # Limit to last 10 turns to control token budget on resume.
    for m in list(history)[-10:]:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        who = "КЛИЕНТ" if role == "user" else "ВЫ"
        hist_lines.append(f"{who}: {content}")
    history_block = "\n".join(hist_lines) or "(диалог только начинается)"

    return (
        "Текущее состояние описания бизнеса:\n\n"
        f"services: {_fmt_list(current.get('services'))}\n"
        f"geo_primary: {_fmt_list(current.get('geo_primary'))}\n"
        f"geo_secondary: {_fmt_list(current.get('geo_secondary'))}\n"
        f"narrative_ru: {current.get('narrative_ru') or '(пусто)'}\n\n"
        "История диалога (последние реплики):\n"
        f"{history_block}\n\n"
        f"Последнее сообщение клиента:\n«{latest_user_message}»\n\n"
        "Обновите состояние согласно правке и верните через tool "
        "`update_business_draft`."
    )


# ── Validation / normalization ───────────────────────────────────────────

# Split on explicit separators (commas, slashes, semicolons) — NOT on
# whitespace, because multi-word geos like "красная поляна" are legal.
_WORD_SPLIT_RE = re.compile(r"[/,;]+")
_ALLOWED_CHARS_RE = re.compile(r"[^а-яёa-z0-9\-\s]")


def _normalize_token(token: str, *, max_words: int = 2) -> str:
    """Lowercase, strip punctuation, collapse whitespace, truncate to N words."""
    t = (token or "").strip().lower()
    t = _ALLOWED_CHARS_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    parts = t.split(" ")
    if len(parts) > max_words:
        parts = parts[:max_words]
    return " ".join(parts)


def _normalize_list(
    values: Any, *, max_len: int, max_words: int = 2,
) -> list[str]:
    """Split, normalize, dedupe, truncate a list of strings."""
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        # Defence-in-depth: if LLM returned "багги, экспедиции" as ONE
        # item instead of two, split on comma/semicolon.
        for piece in _WORD_SPLIT_RE.split(raw):
            norm = _normalize_token(piece, max_words=max_words)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
            if len(out) >= max_len:
                return out
    return out


def validate_draft(patch: dict[str, Any]) -> dict[str, Any]:
    """Normalize LLM-proposed draft, enforce caps + invariants."""
    services = _normalize_list(
        patch.get("services"), max_len=MAX_SERVICES, max_words=2,
    )
    # Geos can be longer ("красная поляна", "новый афон") — allow 2 words.
    geo_primary = _normalize_list(
        patch.get("geo_primary"), max_len=MAX_GEO_PRIMARY, max_words=2,
    )
    geo_secondary_raw = _normalize_list(
        patch.get("geo_secondary"), max_len=MAX_GEO_SECONDARY, max_words=2,
    )
    primary_set = set(geo_primary)
    # Primary wins on overlap — same geo can't be both.
    geo_secondary = [g for g in geo_secondary_raw if g not in primary_set]

    narrative = str(patch.get("narrative_ru") or "").strip()[:MAX_NARRATIVE_CHARS]

    return {
        "services": services,
        "geo_primary": geo_primary,
        "geo_secondary": geo_secondary,
        "narrative_ru": narrative,
    }


# ── Dataclasses ──────────────────────────────────────────────────────────

@dataclass
class InitialMessageResult:
    message_ru: str = ""
    status: str = "ok"  # "ok" | "llm_failed" | "empty_understanding"
    error: str | None = None
    cost_usd: float = 0.0


@dataclass
class RefineResult:
    reply_ru: str = ""
    draft: dict[str, Any] = field(default_factory=dict)
    needs_more_info: bool = True
    status: str = "ok"  # "ok" | "llm_failed" | "malformed" | "capped" | "short_circuit"
    error: str | None = None
    cost_usd: float = 0.0


# ── Entry points ─────────────────────────────────────────────────────────

def build_initial_message(
    domain: str,
    display_name: str | None,
    understanding: dict[str, Any],
    *,
    caller: Any = None,
) -> InitialMessageResult:
    """Generate the first chat message from the one-shot understanding blob."""
    if not understanding or not understanding.get("narrative_ru"):
        return InitialMessageResult(
            status="empty_understanding",
            error="Understanding not ready — run BusinessUnderstandingAgent first.",
        )

    if caller is None:
        try:
            from app.agents.llm_client import call_plain as caller_fn
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_chat.llm_import_failed err=%s", exc)
            return InitialMessageResult(
                status="llm_failed",
                error=f"LLM client import failed: {exc}",
            )
        caller = caller_fn

    user_msg = build_initial_user_prompt(domain, display_name, understanding)

    try:
        text, usage = caller(
            model_tier="cheap",
            system=INITIAL_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_chat.initial_llm_failed err=%s", exc)
        return InitialMessageResult(
            status="llm_failed",
            error=str(exc),
        )

    cost = float((usage or {}).get("cost_usd") or 0.0)
    return InitialMessageResult(
        message_ru=str(text or "").strip()[:MAX_MESSAGE_CHARS],
        status="ok",
        cost_usd=cost,
    )


def refine_draft(
    current: dict[str, Any],
    history: Sequence[dict[str, str]],
    latest_user_message: str,
    *,
    round_number: int = 1,
    caller: Any = None,
) -> RefineResult:
    """Run one refinement turn. Returns new reply + normalized draft."""
    if round_number > MAX_ROUNDS:
        return RefineResult(
            reply_ru=(
                "Мы с вами уже обсудили много правок. Фиксирую текущую версию — "
                "если нужно, поправим позже в настройках."
            ),
            draft=validate_draft(current or {}),
            needs_more_info=False,
            status="capped",
        )

    # Server-side short-circuit for obvious confirmations — saves an LLM call.
    if CONFIRM_REGEX.match(latest_user_message or ""):
        return RefineResult(
            reply_ru=(
                "Отлично, фиксирую описание бизнеса. Перехожу к анализу "
                "запросов и позиций."
            ),
            draft=validate_draft(current or {}),
            needs_more_info=False,
            status="short_circuit",
        )

    if caller is None:
        try:
            from app.agents.llm_client import call_with_tool as caller_fn
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_chat.llm_import_failed err=%s", exc)
            return RefineResult(
                status="llm_failed",
                error=f"LLM client import failed: {exc}",
                draft=validate_draft(current or {}),
            )
        caller = caller_fn

    user_msg = build_refine_user_prompt(current or {}, history, latest_user_message)

    try:
        tool_input, usage = caller(
            model_tier="cheap",
            system=REFINE_SYSTEM_PROMPT,
            user_message=user_msg,
            tool=REFINE_TOOL,
            max_tokens=1500,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_chat.refine_llm_failed err=%s", exc)
        return RefineResult(
            status="llm_failed",
            error=str(exc),
            draft=validate_draft(current or {}),
        )

    cost = float((usage or {}).get("cost_usd") or 0.0)

    if not isinstance(tool_input, dict):
        return RefineResult(
            status="malformed",
            error="LLM returned non-dict tool_input",
            draft=validate_draft(current or {}),
            cost_usd=cost,
        )

    patch = tool_input.get("understanding_patch") or {}
    draft = validate_draft(patch if isinstance(patch, dict) else {})
    reply = str(tool_input.get("reply_ru") or "").strip()[:MAX_MESSAGE_CHARS]
    needs_more = bool(tool_input.get("needs_more_info", True))

    return RefineResult(
        reply_ru=reply,
        draft=draft,
        needs_more_info=needs_more,
        status="ok",
        cost_usd=cost,
    )


__all__ = [
    "MAX_ROUNDS",
    "InitialMessageResult",
    "RefineResult",
    "build_initial_message",
    "refine_draft",
    "validate_draft",
    "CONFIRM_REGEX",
]
