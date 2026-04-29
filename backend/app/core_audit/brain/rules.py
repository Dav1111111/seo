"""Brain — rules-based prioritizer.

Turns a `BrainSnapshot` (pure facts) into a `Plan` of `Action`s that
the owner sees in the UI. Every Action's text is a template with real
counts substituted in, every Action carries a `link_to` url that points
the owner straight at the module where the work happens.

No LLM, no AI-generated copy. The owner-facing text is reviewable in
this file, in Russian, by a human. If a rule's wording is wrong, you
edit it here — there's no model to retrain.

Severity ladder:
  critical → there is something actively HARMING the site (wrong
             pages indexed, spam queries pulling visibility away)
  high     → significant opportunity that the system has high
             confidence in (validated missing landings, pages flat-out
             not indexed)
  medium   → routine work that improves quality (run reviews, apply
             pending recommendations)
  low      → housekeeping
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core_audit.brain.snapshot import BrainSnapshot


Severity = Literal["critical", "high", "medium", "low"]


@dataclass
class Action:
    """One owner-facing item in the plan.

    `evidence` carries the raw counts the rule used to fire — the UI
    can show them verbatim so the owner sees on what basis the system
    pushed this. No prose summary, no LLM-rewritten reasoning.
    """
    id: str               # stable id (kind:detail) so UI can dedupe
    severity: Severity
    title: str            # short imperative ru: «Создай 4 страницы…»
    body_ru: str          # 1-2 sentences of context, templated
    link_to: str          # frontend url to the module where to act
    link_label: str       # CTA text for the link button
    evidence: dict[str, int | str | float | None] = field(default_factory=dict)


@dataclass
class Plan:
    site_id: str
    domain: str
    actions: list[Action]
    diagnostics: list[str]  # owner-readable «module X hasn't run yet»
    computed_at: str        # iso8601


# ── Severity ordering ────────────────────────────────────────────────


_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sort_key(a: Action) -> tuple[int, str]:
    return (_SEV_ORDER.get(a.severity, 9), a.id)


# ── Pluralisation helper (keep brain self-contained) ────────────────


def _ru_plural(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural: 1 страница / 2 страницы / 5 страниц."""
    n = abs(n)
    n10 = n % 10
    n100 = n % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not (12 <= n100 <= 14):
        return forms[1]
    return forms[2]


# ── Rules ────────────────────────────────────────────────────────────


def _rule_indexation_coverage(snap: BrainSnapshot) -> Action | None:
    """Pages NOT in Yandex index (and not deliberately excluded) are
    a hard ranking blocker — surface as critical when the gap is real.

    Skipped when no Webmaster URL data has arrived yet (everything
    `unknown`) — that's a coverage problem of the indexation collector,
    not an owner action.
    """
    idx = snap.indexation
    if idx.pages_total == 0:
        return None
    # If everything is `unknown`, the per-URL Webmaster check just
    # hasn't run — silent.
    if idx.pages_unknown == idx.pages_total:
        return None
    not_indexed = idx.pages_total - idx.pages_in_index - idx.pages_excluded
    if not_indexed <= 0:
        return None
    page_word = _ru_plural(not_indexed, ("страница", "страницы", "страниц"))
    body = (
        f"{not_indexed} {page_word} не подтверждены в индексе Яндекса. "
        f"Их нельзя найти через поиск, даже если в sitemap. "
        f"Открой индексацию, посмотри причины и нажми «Проверить URL»."
    )
    return Action(
        id="indexation:not_indexed",
        severity="critical" if not_indexed >= 3 else "high",
        title=(
            f"Верни в индекс {not_indexed} {page_word}"
        ),
        body_ru=body,
        link_to="/studio/indexation",
        link_label="К индексации",
        evidence={
            "pages_total": idx.pages_total,
            "in_index": idx.pages_in_index,
            "not_indexed": not_indexed,
            "excluded": idx.pages_excluded,
        },
    )


def _rule_harmful_visibility(snap: BrainSnapshot) -> Action | None:
    """Spam + disputed queries we already classified ourselves as
    «not ours» but still rank for. Severity scales with count.

    We don't know per-query position here (that lives in daily_metrics,
    out of scope for the brain — the harmful module already shows it).
    Instead, we surface the known classification totals and link out.
    """
    q = snap.queries
    bad = q.spam + q.disputed
    if bad == 0:
        return None
    if q.total == 0:
        return None
    share_pct = bad / q.total * 100.0
    word = _ru_plural(bad, ("вредный запрос", "вредных запроса", "вредных запросов"))
    body = (
        f"{bad} из {q.total} запросов помечены как «не мои» "
        f"(спам {q.spam} + спорные {q.disputed}). "
        f"Это {share_pct:.0f}% видимости — Яндекс ассоциирует тебя с "
        f"чужой темой. Открой отчёт, чтобы увидеть, какие страницы "
        f"тянут эти запросы и что в них переписать."
    )
    sev: Severity
    if bad >= 20 or share_pct >= 40.0:
        sev = "critical"
    elif bad >= 8 or share_pct >= 20.0:
        sev = "high"
    else:
        sev = "medium"
    return Action(
        id="queries:harmful",
        severity=sev,
        title=f"Почисти {bad} {word} в выдаче",
        body_ru=body,
        link_to="/studio/queries/harmful",
        link_label="К отчёту",
        evidence={
            "spam": q.spam,
            "disputed": q.disputed,
            "total": q.total,
            "share_pct": round(share_pct, 1),
        },
    )


def _rule_missing_landings(snap: BrainSnapshot) -> Action | None:
    """Services that exist in narrative but lack a dedicated page.
    The missing_landings module already enforces evidence — we trust
    its output here without re-validation."""
    m = snap.missing_landings
    if m.total == 0:
        return None
    # Quote up to 3 service names for the body. They came verbatim
    # from the LLM but were validated against narrative — safe to show.
    names = [str(it.get("service_name") or "").strip() for it in m.items]
    names = [n for n in names if n][:3]
    sample = ", ".join(f"«{n}»" for n in names)
    word = _ru_plural(m.total, ("услуга", "услуги", "услуг"))
    pwd = _ru_plural(m.high_priority, ("важная", "важные", "важных"))
    body_parts = [
        f"{m.total} {word} упомянуты в описании бизнеса, но "
        f"отдельной страницы под них нет."
    ]
    if m.high_priority:
        body_parts.append(
            f"Из них {m.high_priority} {pwd} приоритета "
            f"(нужно делать первыми)."
        )
    if sample:
        body_parts.append(f"Например: {sample}.")
    sev: Severity
    if m.high_priority >= 3:
        sev = "critical"
    elif m.high_priority >= 1 or m.total >= 4:
        sev = "high"
    else:
        sev = "medium"
    return Action(
        id="missing_landings:create",
        severity=sev,
        title=f"Создай {m.total} {word} без страницы",
        body_ru=" ".join(body_parts),
        link_to="/studio/competitors",
        link_label="Открыть список",
        evidence={
            "total": m.total,
            "high": m.high_priority,
            "medium": m.medium_priority,
            "low": m.low_priority,
        },
    )


def _rule_pages_without_review(snap: BrainSnapshot) -> Action | None:
    """Pages we never asked Reviewer about. Each unreviewed page is a
    blind spot — the owner can't see what the LLM thinks of its title,
    H1, schema. Surface as medium because reviews don't fix anything by
    themselves; they unblock the next round of recommendations."""
    r = snap.review
    if r.pages_without_review <= 0:
        return None
    if snap.indexation.pages_total == 0:
        return None
    word = _ru_plural(
        r.pages_without_review, ("страница", "страницы", "страниц"),
    )
    body = (
        f"{r.pages_without_review} {word} никогда не проходили ревью. "
        f"LLM не сравнивал их с профилем бизнеса — рекомендаций по ним "
        f"не появится, пока не запустишь ревью. Бесплатно, если контент "
        f"не менялся."
    )
    return Action(
        id="review:unreviewed",
        severity="medium",
        title=f"Запусти ревью {r.pages_without_review} {word}",
        body_ru=body,
        link_to="/studio/pages",
        link_label="К списку страниц",
        evidence={
            "pages_with_review": r.pages_with_review,
            "pages_without_review": r.pages_without_review,
        },
    )


def _rule_pending_recs(snap: BrainSnapshot) -> Action | None:
    """Recommendations sitting in `pending` status — already paid for,
    waiting for owner action. High-priority pending get loud surfacing."""
    r = snap.review
    if r.recs_pending <= 0:
        return None
    word = _ru_plural(r.recs_pending, ("рекомендация", "рекомендации", "рекомендаций"))
    high_n = r.recs_high_priority_pending
    if high_n > 0:
        sev: Severity = "high"
        body = (
            f"{r.recs_pending} {word} ждут решения, из них "
            f"{high_n} с высоким приоритетом. "
            f"Применил — отметь, отметка ставит замер «до» и через "
            f"14 дней покажет дельту."
        )
    else:
        sev = "medium"
        body = (
            f"{r.recs_pending} {word} ждут решения. "
            f"Открой страницы, отметь applied / deferred / dismissed."
        )
    return Action(
        id="review:pending_recs",
        severity=sev,
        title=f"Разбери {r.recs_pending} {word}",
        body_ru=body,
        link_to="/studio/pages",
        link_label="К страницам с рекомендациями",
        evidence={
            "pending": r.recs_pending,
            "high_priority_pending": high_n,
        },
    )


def _rule_followup_due(snap: BrainSnapshot) -> Action | None:
    """Outcomes applied but never measured. The 14-day cycle was the
    whole point of marking «applied». Surfacing here nudges the owner
    when the follow-up is overdue."""
    o = snap.outcomes
    if o.pending_followup <= 0:
        return None
    word = _ru_plural(o.pending_followup, ("замер", "замера", "замеров"))
    return Action(
        id="outcomes:followup",
        severity="low",
        title=f"Замер до/после: {o.pending_followup} {word}",
        body_ru=(
            f"{o.pending_followup} {word} ждут замера через 14 дней "
            f"после внедрения. Это автоматом — модуль «До / После» "
            f"подтянет метрики, как только пройдёт срок."
        ),
        link_to="/studio/outcomes",
        link_label="К замерам",
        evidence={
            "pending_followup": o.pending_followup,
            "applied_total": o.applied_total,
        },
    )


# ── Diagnostics: flag modules that haven't run yet ───────────────────


def _build_diagnostics(snap: BrainSnapshot) -> list[str]:
    """Honest «what we don't know yet» list. CONCEPT §5: explain
    absences. If the owner sees an empty plan, these explain why."""
    out: list[str] = []
    if snap.queries.unclassified == snap.queries.total and snap.queries.total > 0:
        out.append(
            "Запросы пока не классифицированы — запусти модуль «Релевантность» "
            "в Студии запросов."
        )
    if snap.queries.total == 0:
        out.append(
            "Запросов в БД нет — собери их в Webmaster через Pipeline."
        )
    if snap.indexation.pages_unknown == snap.indexation.pages_total \
            and snap.indexation.pages_total > 0:
        out.append(
            "Per-URL индексация ещё не сверена — открой Индексацию и "
            "нажми «Webmaster: статус каждого URL»."
        )
    if not snap.missing_landings.items:
        out.append(
            "Сканирование «Услуги без страниц» ещё не запускалось — "
            "открой Конкуренты и запусти его."
        )
    if snap.queries.with_volume == 0 and snap.queries.total > 0:
        out.append(
            "Wordstat-объёмы не собраны — без них приоритеты по запросам "
            "идут наугад. Запусти сбор в /studio/queries."
        )
    return out


# ── Top-level entry point ─────────────────────────────────────────────


_RULES = (
    _rule_indexation_coverage,
    _rule_harmful_visibility,
    _rule_missing_landings,
    _rule_pages_without_review,
    _rule_pending_recs,
    _rule_followup_due,
)


def build_plan(snap: BrainSnapshot, *, max_actions: int = 5) -> Plan:
    """Apply each rule, drop None, sort by severity, cap at N."""
    actions: list[Action] = []
    for rule in _RULES:
        a = rule(snap)
        if a is not None:
            actions.append(a)
    actions.sort(key=_sort_key)
    actions = actions[:max_actions]

    return Plan(
        site_id=snap.site_id,
        domain=snap.domain,
        actions=actions,
        diagnostics=_build_diagnostics(snap),
        computed_at=snap.computed_at.isoformat(),
    )


__all__ = ["Action", "Plan", "Severity", "build_plan"]
