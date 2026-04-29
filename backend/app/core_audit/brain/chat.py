"""Brain chat — Phase B.

Owner clicks «Спросить» on a brain action and gets a focused chat
with an LLM whose ONLY context is:
  - the action that was clicked (title, body, what_to_do, examples,
    evidence)
  - relevant facts from the snapshot — but only the slice that pertains
    to THIS action's id (don't dump the whole snapshot, that's noise)
  - the conversation history so far

The LLM's job is to ANSWER QUESTIONS about the action — not to invent
new recommendations, not to opine on unrelated topics, not to act
chatty. The system prompt enforces this hard. If the user asks
something the data can't answer, the model says so honestly and
points to the module to check.

Why a separate file: the rules layer must stay LLM-free. This module
is the ONE place where the brain talks to a model, and it's strictly
read-only — it never writes, never plans, never decides.

Cost shape (Haiku):
  per turn ~$0.003 (input ~600 tokens, output ~250 tokens)
  10-turn conversation ~$0.03
"""

from __future__ import annotations

from typing import Any

from app.agents.llm_client import call_plain
from app.core_audit.brain.rules import Action
from app.core_audit.brain.snapshot import BrainSnapshot


# ── Constants ────────────────────────────────────────────────────────


MAX_HISTORY_MESSAGES = 12   # owner-side messages, server collapses if exceeded
MAX_REPLY_TOKENS = 800
MAX_USER_MESSAGE_CHARS = 1200  # per turn; longer is almost always noise


# ── System prompt ────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
Ты — внутренний помощник в SEO-инструменте. Владелец сайта тыкнул
кнопку «Спросить» на конкретном действии, которое предложила система.
Твоя задача — ответить на его вопрос ТОЛЬКО опираясь на данные,
которые тебе передали ниже.

Тон:
  - Простым языком, как живой человек. Без жаргона.
  - На «ты», без формальностей.
  - Коротко. Если можно ответить в 1-2 предложениях — отвечай в 1-2.
  - Не будь чрезмерно бодрым (никаких «Отличный вопрос!»).

Жёсткие правила:

  1. НЕ ПРИДУМЫВАЙ. Ты видишь только то, что дано в блоке КОНТЕКСТ.
     Если вопрос не покрывается данными — скажи это прямо: «Этого я
     не знаю, проверь в модуле X». Не додумывай позиции запросов,
     цены, объёмы и любые числа, которых в КОНТЕКСТЕ нет.

  2. НЕ СОВЕТУЙ ДРУГИЕ ДЕЙСТВИЯ. Если владелец спрашивает «а что ещё
     мне сделать?» — отсылай к плану на /studio. Не предлагай свои
     рекомендации помимо того, что уже есть в действии.

  3. ЦИТИРУЙ КОНКРЕТНОЕ. Когда отвечаешь про запросы / страницы /
     услуги — называй их по имени из примеров. «"джинсы багги"», а не
     «один из вредных запросов».

  4. РАЗБИРАЙ ТЕРМИНЫ. Если владелец спросил «а что такое индексация»,
     «что значит вредная видимость» — объясни на пальцах. Это твоя
     единственная свобода — переводить с системного на человеческий.

  5. КОГДА ВЛАДЕЛЕЦ НЕ СОГЛАСЕН. Если он говорит «нет, "прокат сочи"
     — это мой запрос», поверь ему: скажи «понял, тогда поправь
     классификацию руками — открой запрос и нажми «отметить как мой»».
     Не спорь.

Запрещено:
  - Писать длинные планы или списки шагов сверх того, что уже в
    «Что делать» действия.
  - Давать общие SEO-советы из тренировочных данных.
  - Гарантировать рост позиций / трафика. Это всегда «вероятно» / «по
    нашим данным».
"""


# ── Action → context block ───────────────────────────────────────────


def _format_action_block(action: Action) -> str:
    parts = [
        f"ДЕЙСТВИЕ: {action.title}",
        f"  важность: {action.severity}",
        f"  пояснение: {action.body_ru}",
        f"  что делать: {action.what_to_do_ru}",
    ]
    if action.examples:
        parts.append("  примеры:")
        for ex in action.examples[:5]:
            label = ex.get("label", "")
            kind = ex.get("kind", "")
            hint = ex.get("hint") or ""
            line = f"    - [{kind}] {label}"
            if hint:
                line += f" — {hint}"
            parts.append(line)
    if action.evidence:
        ev_pairs = [
            f"{k}={v}"
            for k, v in action.evidence.items()
            if v is not None
            and isinstance(v, (str, int, float, bool))
        ]
        if ev_pairs:
            parts.append("  цифры за этим: " + ", ".join(ev_pairs))
    return "\n".join(parts)


def _format_snapshot_slice(action_id: str, snap: BrainSnapshot) -> str:
    """Surface only the snapshot slice relevant to this action.
    Earlier we dumped everything — that diluted attention and the
    model started invoking unrelated facts. Hand-pick per action."""
    out: list[str] = ["ОБЩАЯ КАРТИНА ПО САЙТУ:"]
    out.append(f"  домен: {snap.domain}")

    if action_id.startswith("indexation"):
        idx = snap.indexation
        out.extend([
            f"  всего страниц: {idx.pages_total}",
            f"  в индексе Яндекса: {idx.pages_in_index}",
            f"  исключено Яндексом: {idx.pages_excluded}",
            f"  пока неизвестно: {idx.pages_unknown}",
        ])
        if idx.sample_excluded:
            out.append("  примеры исключённых страниц:")
            for ex in idx.sample_excluded[:3]:
                out.append(
                    f"    - {ex.get('url', '')} (причина: {ex.get('reason', '—')})",
                )

    elif action_id.startswith("queries"):
        q = snap.queries
        out.extend([
            f"  запросов всего: {q.total}",
            f"  «мои»: {q.own}",
            f"  смежные: {q.adjacent}",
            f"  спорные: {q.disputed}",
            f"  спам: {q.spam}",
            f"  ещё не разобраны: {q.unclassified}",
        ])
        if q.sample_own:
            out.append("  что система считает «моим»: " + ", ".join(
                f"«{w}»" for w in q.sample_own[:5]
            ))

    elif action_id.startswith("missing_landings"):
        m = snap.missing_landings
        out.extend([
            f"  услуг без отдельной страницы: {m.total}",
            f"  важных: {m.high_priority}",
            f"  средних: {m.medium_priority}",
            f"  несрочных: {m.low_priority}",
        ])

    elif action_id.startswith("review"):
        r = snap.review
        out.extend([
            f"  страниц с ревью: {r.pages_with_review}",
            f"  страниц без ревью: {r.pages_without_review}",
            f"  ожидающих рекомендаций: {r.recs_pending}",
            f"  из них высокого приоритета: {r.recs_high_priority_pending}",
        ])

    elif action_id.startswith("outcomes"):
        o = snap.outcomes
        out.extend([
            f"  всего применённых правок: {o.applied_total}",
            f"  применено за 14 дней: {o.applied_last_14d}",
            f"  ждут замера через 14 дней: {o.pending_followup}",
        ])

    return "\n".join(out)


# ── Public API ───────────────────────────────────────────────────────


def build_user_message(
    *,
    action: Action,
    snap: BrainSnapshot,
    history: list[dict[str, str]],
    new_message: str,
) -> str:
    """Compose the single user-message string for `call_plain`. We
    don't use the SDK's multi-turn messages here on purpose — the
    server is stateless, and prompt caching on the system block
    already covers the bulk of the recurring cost."""
    blocks = [
        _format_action_block(action),
        "",
        _format_snapshot_slice(action.id, snap),
        "",
    ]
    if history:
        blocks.append("ИСТОРИЯ РАЗГОВОРА:")
        for turn in history[-MAX_HISTORY_MESSAGES:]:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            tag = "ВЛАДЕЛЕЦ" if role == "user" else "ТЫ"
            blocks.append(f"{tag}: {content}")
        blocks.append("")
    blocks.append(f"ВЛАДЕЛЕЦ СЕЙЧАС СПРАШИВАЕТ: {new_message.strip()}")
    blocks.append("")
    blocks.append("Ответь ему прямо, в 1-3 предложениях. На русском.")
    return "\n".join(blocks)


def chat_about_action(
    *,
    action: Action,
    snap: BrainSnapshot,
    history: list[dict[str, str]],
    new_message: str,
) -> dict[str, Any]:
    """One-turn chat exchange. Returns
    `{reply, cost_usd, model, input_tokens, output_tokens}`."""
    new_message = (new_message or "").strip()
    if not new_message:
        raise ValueError("empty message")
    if len(new_message) > MAX_USER_MESSAGE_CHARS:
        new_message = new_message[:MAX_USER_MESSAGE_CHARS] + " […обрезано]"

    user_msg = build_user_message(
        action=action,
        snap=snap,
        history=history or [],
        new_message=new_message,
    )

    reply, usage = call_plain(
        model_tier="cheap",
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        max_tokens=MAX_REPLY_TOKENS,
    )

    return {
        "reply": (reply or "").strip(),
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "model": usage.get("model") or "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_REPLY_TOKENS",
    "MAX_USER_MESSAGE_CHARS",
    "SYSTEM_PROMPT",
    "build_user_message",
    "chat_about_action",
]
