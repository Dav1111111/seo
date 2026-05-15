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

    Three fields owner sees:
      title    — what's wrong, in one sentence
      body_ru  — 2-3 sentences explaining «и что» в живом тоне.
                 Templated — no LLM, no prose generation.
      what_to_do_ru — 1 sentence imperative: «открой X, нажми Y».
      examples — concrete rows from the database (URLs, queries,
                 service names) that this action is about. UI shows
                 them under the body so the count gets a face.

    `evidence` is the raw count receipt — for the «основание» row.
    `in_focus` (Phase E step 2): True iff at least one example /
                signal of this action matches the site's strategic
                focus (products / regions / query_signals). Drives
                the focus-first plan sort. Without focus → False on
                everything (and sort falls back to pure severity).
    """
    id: str               # stable id (kind:detail) so UI can dedupe
    severity: Severity
    title: str            # short headline ru: «У тебя 82% видимости…»
    body_ru: str          # 2-3 conversational sentences explaining why
    what_to_do_ru: str    # 1 imperative sentence — next concrete step
    link_to: str          # frontend url to the module where to act
    link_label: str       # CTA text for the link button
    examples: list[dict[str, str]] = field(default_factory=list)
    evidence: dict[str, int | str | float | None] = field(default_factory=dict)
    in_focus: bool = False


@dataclass
class Plan:
    site_id: str
    domain: str
    actions: list[Action]
    diagnostics: list[str]  # owner-readable «module X hasn't run yet»
    computed_at: str        # iso8601
    focus_label: str | None = None  # «Сейчас работаем над: …» — UI banner copy


# ── Severity ordering + focus-aware sort ────────────────────────────


_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sort_key(a: Action) -> tuple[int, int, str]:
    """Sort key: (focus-bucket, severity, id).

    Focus-bucket = 0 when action.in_focus, 1 otherwise. So the order is:

        critical-in-focus → high-in-focus → medium-in-focus → low-in-focus
        critical-out-of-focus → high-out → medium-out → low-out

    This way, a clean «in-focus medium» beats a noisy «out-of-focus
    critical» — exactly the behaviour the owner asked for: «сейчас
    мне важно ТОЛЬКО Абхазия, остальное потом». Without focus,
    everything is in_focus=False, and the order falls back to pure
    severity (since the bucket is constant).
    """
    bucket = 0 if a.in_focus else 1
    return (bucket, _SEV_ORDER.get(a.severity, 9), a.id)


# ── Focus-match helper ──────────────────────────────────────────────


def _focus_tokens(focus) -> list[str]:  # type: ignore[no-untyped-def]
    """Flat list of substrings the focus is interested in. Lowercased,
    stripped. Used by `_signals_in_focus` for substring matching."""
    if focus is None:
        return []
    out: list[str] = []
    for collection in (focus.products, focus.regions, focus.query_signals):
        for item in collection or []:
            s = (item or "").strip().lower()
            if s:
                out.append(s)
    # Deduped while preserving order — the longer ones first so we
    # don't match «крым» when the focus item is «крымская экспедиция».
    seen: set[str] = set()
    deduped: list[str] = []
    for s in sorted(out, key=len, reverse=True):
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


def _signals_in_focus(signals: list[str], tokens: list[str]) -> bool:
    """True iff any signal substring-matches any focus token. Both
    sides are lowercased. We don't require token boundaries — for
    URLs and Russian morphology partial match is the realistic bar
    («багги-абхазия», «багги абхазии», «багги-абхазский маршрут»
    all share the substring «багги»)."""
    if not tokens or not signals:
        return False
    for sig in signals:
        s = (sig or "").strip().lower()
        if not s:
            continue
        for t in tokens:
            if t in s:
                return True
    return False


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


def _rule_indexation_coverage(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Pages NOT in Yandex index (and not deliberately excluded) are
    a hard ranking blocker — surface as critical when the gap is real.

    Math: `not_indexed = total - in_index - excluded - unknown`.
    `unknown` (Webmaster hasn't reported yet) is NOT «не в индексе» —
    earlier the rule treated it as such and fired false-positive
    critical when per-URL data was partial. Surface unknown via
    diagnostics instead.

    Min-size guard: tiny sites where 1-2 pages haven't indexed yet are
    just normal Yandex latency, not an owner problem. Stay silent under
    a soft threshold so the brain doesn't cry wolf on day-one sites.
    """
    idx = snap.indexation
    if idx.pages_total == 0:
        return None
    # If everything is `unknown`, the per-URL Webmaster check just
    # hasn't run — silent (diagnostics layer surfaces this).
    if idx.pages_unknown == idx.pages_total:
        return None
    # Prefer the snapshot's directly-counted field if/when it exists
    # (forward-compat: snapshot may add `confirmed_not_indexed` so we
    # stop relying on subtraction). When buckets don't add up to
    # `pages_total` within a small tolerance, we trust the smaller
    # subtraction result but log so the discrepancy is visible to
    # whoever debugs the snapshot loader.
    direct = getattr(idx, "confirmed_not_indexed", None)
    subtracted = max(
        0,
        idx.pages_total - idx.pages_in_index - idx.pages_excluded - idx.pages_unknown,
    )
    bucket_sum = (
        idx.pages_in_index + idx.pages_excluded + idx.pages_unknown + subtracted
    )
    if abs(bucket_sum - idx.pages_total) > 2:
        import logging
        logging.getLogger(__name__).debug(
            "indexation buckets don't sum: total=%s in_index=%s excluded=%s "
            "unknown=%s subtracted=%s (delta=%s)",
            idx.pages_total, idx.pages_in_index, idx.pages_excluded,
            idx.pages_unknown, subtracted, bucket_sum - idx.pages_total,
        )
    if isinstance(direct, int) and direct >= 0:
        confirmed_not_indexed = direct
    else:
        confirmed_not_indexed = subtracted
    if confirmed_not_indexed <= 0:
        return None
    # Quiet-bar: < 10-page site with ≤ 2 unindexed is normal indexing
    # latency, not a fixable issue. We'd be alarmist to flag it.
    if idx.pages_total < 10 and confirmed_not_indexed <= 2:
        return None

    page_word = _ru_plural(confirmed_not_indexed, ("страница", "страницы", "страниц"))
    title = (
        f"Яндекс не видит {confirmed_not_indexed} "
        f"{_ru_plural(confirmed_not_indexed, ('твою страницу', 'твои страницы', 'твоих страниц'))}"
    )
    body = (
        f"На сайте {idx.pages_total} "
        f"{_ru_plural(idx.pages_total, ('страница', 'страницы', 'страниц'))}, "
        f"из них {idx.pages_in_index} в поиске Яндекса. "
        f"А {confirmed_not_indexed} {page_word} — нет: их нельзя найти "
        f"в поиске вообще, даже если они есть в sitemap. "
        f"Это значит, что трафик на них приходит только если кто-то "
        f"знает прямую ссылку."
    )
    examples = [
        {"label": url, "kind": "url"}
        for url in (idx.sample_not_indexed_urls or [])[:3]
    ]
    what_to_do = (
        "Открой раздел «Индексация» — там видно, по каждой странице "
        "почему её нет в индексе. Самые частые причины: запрет в "
        "robots.txt, noindex в коде, или Яндекс пока не дошёл."
    )
    return Action(
        id="indexation:not_indexed",
        severity="critical" if confirmed_not_indexed >= 3 else "high",
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/indexation",
        link_label="К индексации",
        examples=examples,
        evidence={
            "pages_total": idx.pages_total,
            "in_index": idx.pages_in_index,
            "not_indexed": confirmed_not_indexed,
            "excluded": idx.pages_excluded,
            "unknown": idx.pages_unknown,
        },
        in_focus=_signals_in_focus(
            idx.sample_not_indexed_urls or [], focus_tokens,
        ),
    )


def _rule_harmful_visibility(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Spam + disputed queries we already classified ourselves as
    «not ours» but still rank for. Severity scales with count.

    Two corrections vs initial release:

      1. **Share basis = classified queries, not all queries.** Earlier
         we divided by `total`, which inflates «share of visibility»
         on partially-classified sites: spam=5 / total=100 = 5% even if
         within the classified subset spam was 33%. Body text now says
         «из проверенных» so the number is honest.

      2. **`min_total >= 15` guard.** On a 10-query site, 4 spam = 40%
         = critical was alarmist — small samples have noisy ratios.
         Below that threshold we still surface the action (so the
         owner can ack it) but tone severity down to medium / low.

    We don't know per-query position here (that lives in daily_metrics,
    out of scope for the brain — the harmful module already shows it).
    Instead, we surface the known classification totals and link out.
    """
    q = snap.queries
    bad = q.spam + q.disputed
    if bad == 0:
        return None
    classified = q.total - q.unclassified
    if classified <= 0:
        return None
    share_pct = bad / classified * 100.0

    # Build conversational title/body. Goal: owner immediately gets
    # «по большинству запросов меня находят не по моей теме». Copy
    # tiers mirror the severity ladder below so wording stays
    # proportional to the math.
    if share_pct >= 70:
        title = "Яндекс не понимает, кто ты"
        why_short = "Большинство запросов, по которым тебя находят, — не про твою тему."
    elif share_pct >= 40:
        title = "Большая часть видимости — не твоя"
        why_short = "Слишком много запросов, по которым тебя находят, — про чужую тему."
    else:
        title = f"{bad} {_ru_plural(bad, ('вредный запрос', 'вредных запроса', 'вредных запросов'))} в выдаче"
        why_short = "Часть запросов, по которым тебя находят, — не про твою тему."

    body_parts = [
        f"Из {classified} {_ru_plural(classified, ('запроса', 'запросов', 'запросов'))}, "
        f"по которым люди находят твой сайт, {bad} — про не твою тему "
        f"(спам {q.spam} + спорные {q.disputed}, {share_pct:.0f}%). "
        f"{why_short}",
        "Это значит, что Яндекс не уверен, кто ты, и реже показывает "
        "тебя по нужным запросам.",
    ]
    if q.sample_own:
        own_examples = ", ".join(f"«{w}»" for w in q.sample_own[:3])
        body_parts.append(f"Твоя тема (как мы её видим): {own_examples}.")

    examples = [
        {
            "label": h.get("query_text") or "",
            "kind": h.get("relevance") or "",  # spam | disputed
            "hint": h.get("reason_ru") or "",
        }
        for h in (q.sample_harmful or [])[:3]
    ]

    what_to_do = (
        "Открой «Вредная видимость». Там видно, какая страница тянет "
        "каждый из этих запросов и какие слова из её текста нужно "
        "убрать, чтобы перестать ранжироваться по чужой теме."
    )

    # Severity ladder. Two corrections vs. the earlier OR-based rule:
    #
    #   * critical requires BOTH count and share simultaneously
    #     (bad≥20 AND share≥40). The old `OR` would let 6 spam of 15
    #     classified (= 40%) ring as critical — that's noise on a
    #     small sample, not «всю видимость съели». An additional
    #     branch keeps critical for catastrophic 70%+ situations on
    #     non-trivial samples where the count is still small.
    #   * high/medium thresholds widened so they can fire on share
    #     alone for medium-sized signal, since count alone may lag.
    #
    # Small-site clamp lives below — it can downgrade these but
    # never upgrade.
    sev: Severity
    if (bad >= 20 and share_pct >= 40.0) or (share_pct >= 70.0 and classified >= 5):
        sev = "critical"
    elif bad >= 8 or share_pct >= 30.0:
        sev = "high"
    elif bad >= 5 or share_pct >= 20.0:
        sev = "medium"
    else:
        sev = "low"
    # Small-sample clamp. With <15 classified queries the ratio is
    # noisy, so we cap severity at `medium`. EXCEPTION: a fully-
    # broken small site (≥70% harm, ≥5 classified) gets to be
    # `high` — losing 70%+ of visibility is a real problem even
    # when total volume is modest. We never let a small sample
    # become `critical` (that's reserved for sites with both
    # absolute scale and share).
    if classified <= 15:
        if share_pct >= 70.0 and classified >= 5:
            if sev == "critical":
                sev = "high"
        else:
            if sev in ("critical", "high"):
                sev = "medium"
    # Harmful visibility is about queries we DON'T want — so the
    # in-focus check inverts: if our spam/disputed examples sit
    # «around» focus tokens, that's exactly the harm worth fixing
    # first (they're confusing Yandex about our focus). If they're
    # totally unrelated to the focus topic, it's still harmful but
    # less urgent in the focus phase.
    harmful_query_signals = [
        h.get("query_text", "") for h in (q.sample_harmful or [])
        if isinstance(h, dict)
    ]
    return Action(
        id="queries:harmful",
        severity=sev,
        title=title,
        body_ru=" ".join(body_parts),
        what_to_do_ru=what_to_do,
        link_to="/studio/queries/harmful",
        link_label="К вредной видимости",
        examples=examples,
        evidence={
            "spam": q.spam,
            "disputed": q.disputed,
            "classified": classified,
            "total": q.total,
            "share_pct": round(share_pct, 1),
        },
        in_focus=_signals_in_focus(harmful_query_signals, focus_tokens),
    )


def _rule_missing_landings(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Services that exist in narrative but lack a dedicated page.
    The missing_landings module already enforces evidence — we trust
    its output here without re-validation."""
    m = snap.missing_landings
    if m.total == 0:
        return None

    word = _ru_plural(m.total, ("услуга", "услуги", "услуг"))
    title = (
        f"{m.total} {_ru_plural(m.total, ('твоей услуги', 'твоих услуги', 'твоих услуг'))} "
        f"живёт без своей страницы"
    )
    body_parts = [
        f"Ты говоришь, что у тебя есть {m.total} {word}, "
        f"но на сайте под них нет отдельной страницы — они упоминаются "
        f"только в общем тексте или в попапе.",
        "Яндекс и Гугл попапы не индексируют — значит, по этим услугам "
        "тебя в поиске почти не находят.",
    ]
    if m.high_priority:
        body_parts.append(
            f"Из {m.total} {_ru_plural(m.total, ('услуги', 'услуг', 'услуг'))} "
            f"{m.high_priority} нужно делать первыми — там и спрос есть, "
            f"и описание у тебя уже готово."
        )

    # Examples — actual service names from missing_landings module.
    # They've already passed the substring-evidence filter, so we
    # quote them verbatim without any LLM rewriting.
    examples = []
    for it in m.items[:3]:
        name = (it.get("service_name") or "").strip()
        if not name:
            continue
        examples.append({
            "label": name,
            "kind": (it.get("priority") or "medium"),
            "hint": (it.get("evidence_quote") or "").strip(),
        })

    what_to_do = (
        "Открой «Конкуренты» → секция «Услуги без посадочных страниц». "
        "Там по каждой услуге видно цитату из твоего описания + "
        "предлагаемый URL для новой страницы."
    )

    # Severity: only escalate to `high` when at least one explicitly
    # high-priority landing is missing, OR when the total backlog is
    # large enough that even all-low-priority items add up. Four
    # purely-low-priority items used to fire `high`, which clashed
    # with `pages_without_review` always being `medium` — calibrate
    # so wholly-low backlogs stay medium.
    sev: Severity
    if m.high_priority >= 3:
        sev = "critical"
    elif m.high_priority >= 1 or m.total >= 6:
        sev = "high"
    else:
        sev = "medium"
    # Match focus by service names + their evidence quotes —
    # «Багги-экспедиция в Крым» falls in focus iff focus tokens
    # include «крым». Quotes catch the case where service_name is
    # generic but the supporting quote mentions the focus topic.
    landing_signals: list[str] = []
    for it in m.items[:8]:
        sn = (it.get("service_name") or "").strip()
        if sn:
            landing_signals.append(sn)
        q = (it.get("evidence_quote") or "").strip()
        if q:
            landing_signals.append(q)
    return Action(
        id="missing_landings:create",
        severity=sev,
        title=title,
        body_ru=" ".join(body_parts),
        what_to_do_ru=what_to_do,
        link_to="/studio/competitors",
        link_label="К списку услуг",
        examples=examples,
        evidence={
            "total": m.total,
            "high": m.high_priority,
            "medium": m.medium_priority,
            "low": m.low_priority,
        },
        in_focus=_signals_in_focus(landing_signals, focus_tokens),
    )


def _rule_pages_without_review(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
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
    title = (
        f"{r.pages_without_review} {word} мы ещё не разбирали"
    )
    body = (
        f"На {r.pages_without_review} {word} мы ни разу не запускали "
        f"ревью — это когда система читает страницу и сравнивает её "
        f"с профилем твоего бизнеса (заголовок, текст, мета-описание, "
        f"микроразметка). "
        f"Без ревью у нас нет конкретных рекомендаций по этим страницам."
    )
    examples = [
        {"label": url, "kind": "url"}
        for url in (r.sample_unreviewed_urls or [])[:3]
    ]
    what_to_do = (
        "Открой «Страницы», нажми на любую из непроверенных и "
        "«Запустить ревью». Если контент не менялся с прошлого раза — "
        "ревью не повторится бесплатно (защита от перерасхода)."
    )
    return Action(
        id="review:unreviewed",
        severity="medium",
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/pages",
        link_label="К страницам",
        examples=examples,
        evidence={
            "pages_with_review": r.pages_with_review,
            "pages_without_review": r.pages_without_review,
        },
        in_focus=_signals_in_focus(
            r.sample_unreviewed_urls or [], focus_tokens,
        ),
    )


def _rule_pending_recs(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Recommendations sitting in `pending` status — already paid for,
    waiting for owner action. High-priority pending get loud surfacing.

    `in_focus` is derived from the URLs of pending rec rows; if any
    pending rec's URL matches a focus token, the action sorts ahead
    of out-of-focus items at the same severity. Without this flag,
    in-focus medium actions would jump over high-priority pending
    recs that happen to live on focus pages.
    """
    r = snap.review
    if r.recs_pending <= 0:
        return None
    word = _ru_plural(r.recs_pending, ("рекомендация", "рекомендации", "рекомендаций"))
    high_n = r.recs_high_priority_pending
    title = f"{r.recs_pending} {word} ждут твоего решения"
    if high_n > 0:
        sev: Severity = "high"
        body = (
            f"Система уже проанализировала страницы и предложила "
            f"{r.recs_pending} {word} — что-то поправить в title, h1, "
            f"тексте или микроразметке. Из них {high_n} с высоким "
            f"приоритетом. Если применишь — отметь, и через 14 дней "
            f"мы автоматически замерим, дала ли правка эффект."
        )
    else:
        sev = "medium"
        body = (
            f"Система предложила {r.recs_pending} {word} — что-то "
            f"поправить в title, h1, тексте или микроразметке. "
            f"Открой страницы, отметь применил / отложил / не подходит."
        )
    what_to_do = (
        "Открой «Страницы», заходи в каждую с рекомендациями и решай "
        "по каждому пункту: «применил», «отложить», «не подходит». "
        "Кнопка «применил» автоматически фиксирует метрики «до» — "
        "через 14 дней увидишь дельту."
    )
    # in_focus: any pending rec URL substring-matches a focus token.
    # Read from `top_pending_recommendations` (snapshot.py field), each
    # row carries a `url` string. Missing field / empty list → False,
    # preserving the legacy behaviour for tests that don't pass it.
    rec_urls: list[str] = []
    pending_rows = getattr(r, "top_pending_recommendations", None) or []
    for row in pending_rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("page_url")
        if isinstance(url, str) and url:
            rec_urls.append(url)
    return Action(
        id="review:pending_recs",
        severity=sev,
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/pages",
        link_label="К страницам с рекомендациями",
        evidence={
            "pending": r.recs_pending,
            "high_priority_pending": high_n,
        },
        in_focus=_signals_in_focus(rec_urls, focus_tokens),
    )


def _rule_followup_due(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Outcomes applied but never measured. The 14-day cycle was the
    whole point of marking «applied». Surfacing here nudges the owner
    when the follow-up is overdue.

    Copy adapts to actual age: if the oldest pending follow-up is
    overdue (>14 days), we stop saying «скоро» and acknowledge that
    we're already past the natural window. `in_focus` is derived
    from any URLs the outcomes loader chose to expose (getattr — keeps
    forward-compat if snapshot adds the field, returns False today).
    """
    o = snap.outcomes
    if o.pending_followup <= 0:
        return None
    word = _ru_plural(o.pending_followup, ("замер", "замера", "замеров"))

    # Optional fields — snapshot may add these later. Until then,
    # getattr-default keeps the rule honest without rewriting snapshot.
    outcome_urls_raw = getattr(o, "sample_pending_urls", None) or []
    outcome_urls = [u for u in outcome_urls_raw if isinstance(u, str) and u]
    oldest_days = getattr(o, "oldest_pending_days", None)
    overdue_n = getattr(o, "pending_followup_overdue", None)
    overdue = (
        isinstance(oldest_days, (int, float)) and oldest_days > 14
    )

    if overdue:
        overdue_label = (
            int(overdue_n)
            if isinstance(overdue_n, (int, float)) and overdue_n > 0
            else o.pending_followup
        )
        title = f"{o.pending_followup} {word} ждут дольше 14 дней"
        body = (
            f"Ты применил {o.pending_followup} "
            f"{_ru_plural(o.pending_followup, ('правку', 'правки', 'правок'))} "
            f"и нажал «применил & замерить эффект», но "
            f"{overdue_label} из них уже просрочены — прошло больше "
            f"14 дней, а замер «после» так и не подтянулся. "
            f"Это значит, что Webmaster ещё не отдал свежие метрики "
            f"для этих страниц; загляни в «До / После», там видно по "
            f"каждой правке, чего конкретно не хватает."
        )
    else:
        title = f"Скоро узнаем результат твоих {o.pending_followup} {word}"
        body = (
            f"Ты применил {o.pending_followup} "
            f"{_ru_plural(o.pending_followup, ('правку', 'правки', 'правок'))} "
            f"и нажал «применил & замерить эффект». "
            f"Замеры до/после делаются автоматически через 14 дней "
            f"после применения — здесь ничего делать не надо, просто "
            f"подожди и потом загляни в «До / После»."
        )
    what_to_do = (
        "Ничего делать не нужно. Через 14 дней после каждой правки "
        "метрики (показы, клики, позиции) подтянутся сами и в модуле "
        "«До / После» появится дельта."
    )
    evidence: dict[str, int | str | float | None] = {
        "pending_followup": o.pending_followup,
        "applied_total": o.applied_total,
    }
    if isinstance(oldest_days, (int, float)):
        evidence["oldest_pending_days"] = int(oldest_days)
    return Action(
        id="outcomes:followup",
        severity="low",
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/outcomes",
        link_label="К замерам",
        evidence=evidence,
        in_focus=_signals_in_focus(outcome_urls, focus_tokens),
    )


def _rule_ctr_gap(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Pages ranking but under-clicking — the cheapest behavioral win.

    Yandex 2026 weighs behavioral factors at 30-45% of the formula.
    A page sitting at position 3 with 2% CTR (expected ~10%) is leaking
    traffic on already-earned ranking. Rewriting the title and meta-
    description for that one page typically lifts clicks 30-80% within
    two weeks without any other work — no links, no new content.

    Severity escalation:
      critical  → 1+ critical-severity gaps (≥1000 impressions + ratio<0.35)
      high      → ≥3 high-severity OR 1+ critical
      medium    → ≥1 high-severity gap
      no action → only low-severity gaps (don't bother the owner)
    """
    b = snap.behavioral
    if b.ctr_gaps_total == 0:
        return None

    if b.ctr_gaps_critical > 0:
        sev: Severity = "critical"
    elif b.ctr_gaps_high >= 3:
        sev = "high"
    elif b.ctr_gaps_high >= 1 or b.ctr_gaps_medium >= 3:
        sev = "medium"
    elif b.ctr_gaps_medium >= 1:
        sev = "low"
    else:
        return None

    flagged_total = b.ctr_gaps_critical + b.ctr_gaps_high + b.ctr_gaps_medium
    word_q = _ru_plural(flagged_total, ("запрос", "запроса", "запросов"))
    title = (
        f"{flagged_total} {word_q} недоклика — позиция уже есть, "
        f"но сниппет не цепляет"
    )

    examples: list[dict[str, str]] = []
    for g in b.sample_gaps[:5]:
        examples.append({
            "query": str(g.get("query", "")),
            "details": (
                f"позиция {g.get('avg_position', '—')}, "
                f"CTR {g.get('actual_ctr', 0)}% при ожидаемых "
                f"{g.get('expected_ctr', 0)}% "
                f"({g.get('impressions', 0)} показов)"
            ),
        })

    in_focus = False
    if focus_tokens:
        signals = [str(g.get("query", "")) for g in b.sample_gaps]
        in_focus = _signals_in_focus(signals, focus_tokens)

    body = (
        f"По {flagged_total} {word_q} ты уже в топ-10, но кликают "
        f"в 2-5 раз реже ожидаемого для этой позиции. Это значит "
        f"сниппет (title + описание) не отвечает на интент пользователя. "
        f"Самая дешёвая правка в SEO: переписать title под ту же страницу "
        f"и поднять CTR без работы со ссылками или контентом. "
        f"Под ударом ~{b.impressions_at_risk:,} показов в месяц."
    ).replace(",", " ")
    what_to_do = (
        "Открой Запросы, отсортируй по показам убывая, найди топ-5 "
        "из этого списка и для каждой целевой страницы перепиши "
        "title в формате «{что} в {где} — от {цена} ₽» или с явной "
        "выгодой для интента."
    )

    return Action(
        id="behavioral:ctr_gap",
        severity=sev,
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/queries",
        link_label="К запросам",
        examples=examples,
        evidence={
            "ctr_gaps_total": b.ctr_gaps_total,
            "ctr_gaps_critical": b.ctr_gaps_critical,
            "ctr_gaps_high": b.ctr_gaps_high,
            "ctr_gaps_medium": b.ctr_gaps_medium,
            "impressions_at_risk": b.impressions_at_risk,
        },
        in_focus=in_focus,
    )


def _rule_robots_critical(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Surface critical robots.txt issues as a top-priority action.

    A broken robots.txt is upstream of everything else — if Yandex
    can't parse it, the crawler may drop the whole site or honour
    rules the owner never intended. The dedicated audit module
    (`core_audit/yandex_robots`) publishes per-issue findings into
    `analysis_events.extra["issues"]`; we read the count and surface
    it. Stays silent at zero — never alarmist.

    Severity is `critical` regardless of count: even one
    «Disallow: /» style misfire can wipe out indexation.
    """
    n = snap.robots_critical_issues
    if n <= 0:
        return None

    issue_word = _ru_plural(
        n, ("критическая проблема", "критические проблемы", "критических проблем"),
    )
    title = (
        f"В robots.txt найдено {n} {issue_word} для Яндекса"
    )
    if snap.robots_valid_for_yandex:
        body = (
            f"Аудит robots.txt нашёл {n} {issue_word}, из-за которых "
            f"Яндекс может неправильно понять, какие страницы можно "
            f"индексировать. robots.txt — это самый верх воронки: если "
            f"в нём ошибка, всё остальное (sitemap, canonical, контент) "
            f"для затронутых страниц не имеет значения."
        )
    else:
        body = (
            f"robots.txt сейчас недоступен или не распарсивается — "
            f"плюс к этому аудит уже нашёл {n} {issue_word}. "
            f"Это значит, что мы не можем гарантировать, по каким "
            f"правилам YandexBot обходит сайт прямо сейчас."
        )
    what_to_do = (
        "Открой «Индексация» → блок «Проверка robots.txt для Яндекса». "
        "Там по каждой проблеме видно цитату из файла, объяснение и "
        "конкретную правку. Начни с критических — они блокируют индекс."
    )
    return Action(
        id="robots:critical",
        severity="critical",
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/indexation",
        link_label="К проверке robots.txt",
        evidence={
            "critical_issues": n,
            "valid_for_yandex": snap.robots_valid_for_yandex,
        },
        # robots.txt is site-wide — there's no per-focus signal to
        # match. We default to False so the focus-first sort places it
        # alongside other site-wide criticals.
        in_focus=False,
    )


def _rule_wordstat_partial_coverage(
    snap: BrainSnapshot, focus_tokens: list[str],
) -> Action | None:
    """Wordstat-volume coverage is below 50% — owner can't prioritise.

    Old version of this rule fired only when `with_volume_known == 0`,
    which silently passed through the common middle ground («4 из 13
    собрано»). Now it fires whenever fewer than half the classified
    queries have a Wordstat answer.

    Severity:
      critical → coverage below 20% (almost nothing to prioritise on)
      warning  → coverage 20-49% (we have a partial picture)
      silent   → coverage ≥50% OR no queries at all

    Stays silent when total == 0 (nothing to assess) and when we have
    no «never_fetched» rows (all phrases were tried — owner already
    can't squeeze more from this lever; the diagnostic blurb in
    `_build_diagnostics` still mentions it).
    """
    q = snap.queries
    if q.total == 0:
        return None
    coverage = q.with_volume_known / q.total
    if coverage >= 0.5:
        return None
    if q.never_fetched == 0:
        # No new fetches would help — every phrase was already tried.
        # Surface this through the diagnostics blurb instead, not as
        # an action with no useful CTA.
        return None
    missing = q.total - q.with_volume_known
    sev: Severity = "critical" if coverage < 0.2 else "high"
    word_q = _ru_plural(q.total, ("запроса", "запросов", "запросов"))
    title = (
        f"Wordstat-объёмы собраны только для {q.with_volume_known} из "
        f"{q.total} {word_q}"
    )
    body = (
        f"Без объёма Wordstat нельзя честно приоритизировать запросы "
        f"по реальному спросу. Сейчас {missing} запросов ещё не "
        f"опрашивали в Wordstat — это не «нет спроса», это «не успели "
        f"собраться». Запусти ручную сборку «Обновить объёмы "
        f"Wordstat», или дождись следующего вторника (еженедельный "
        f"автозапуск). Если все запросы много раз пытались собрать и "
        f"Wordstat возвращает пусто, проверь регион в настройках сайта."
    )
    what_to_do = (
        "Открой Запросы и нажми «Обновить объёмы Wordstat». После "
        "сбора плана priority-скоринг сможет сравнить запросы по "
        "реальному спросу."
    )
    return Action(
        id="wordstat:partial_coverage",
        severity=sev,
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/queries",
        link_label="К запросам",
        evidence={
            "total": q.total,
            "with_volume_known": q.with_volume_known,
            "with_demand": q.with_demand,
            "never_fetched": q.never_fetched,
            "coverage_pct": round(coverage * 100, 1),
        },
        # Coverage gap is site-wide — no per-focus signal to match.
        in_focus=False,
    )


# ── Diagnostics: flag modules that haven't run yet ───────────────────


def _build_diagnostics(snap: BrainSnapshot) -> list[str]:
    """Honest «чего я ещё не знаю» list. CONCEPT §5: explain absences.
    Tone: first-person owner-facing — «я ещё не сверил», not «module
    X didn't run». No jargon."""
    out: list[str] = []
    if snap.queries.unclassified == snap.queries.total and snap.queries.total > 0:
        out.append(
            "Я ещё не разложил запросы по полкам (где «твой», где «не твой»). "
            "Зайди в Запросы и нажми «Классифицировать» — после этого "
            "я смогу сказать тебе про вредную видимость."
        )
    if snap.queries.total == 0:
        out.append(
            "У меня пока нет ни одного запроса по сайту. Чтобы они "
            "появились — собери данные из Webmaster в Pipeline."
        )
    if snap.indexation.pages_unknown == snap.indexation.pages_total \
            and snap.indexation.pages_total > 0:
        out.append(
            "Я ещё не сверял каждый URL с индексом Яндекса. "
            "Открой Индексацию и нажми «Webmaster: статус каждого URL» "
            "— тогда я скажу тебе, какие страницы выпали."
        )
    if not snap.missing_landings.items:
        out.append(
            "Я ещё не проверял, все ли твои услуги имеют отдельную "
            "страницу. Открой Конкуренты и запусти «Услуги без страниц» "
            "— одна минута, ~10 центов LLM."
        )
    if snap.queries.with_volume_known == 0 and snap.queries.total > 0:
        out.append(
            "У меня нет данных о том, как часто люди ищут эти запросы "
            "(Wordstat-объёмов). Без этого я не могу сказать, какой "
            "запрос важнее. Запусти сбор Wordstat в /studio/queries."
        )
    # Behavioral signals diagnostic: explain why CTR-gaps are silent.
    # The owner should know whether "no signal" means "all good" or
    # "not enough data yet". 30+ impressions per query is the floor.
    if snap.behavioral.ctr_gaps_total == 0 and snap.queries.total > 0:
        out.append(
            "Я ещё не вижу страниц с просадкой по CTR. Это либо значит, "
            "что у тебя всё в порядке со сниппетами, либо что трафика "
            "пока мало (нужно 30+ показов на запрос за 30 дней). Когда "
            "Webmaster наберёт больше данных — я подсвечу страницы, "
            "где сниппет не цепляет."
        )
    return out


# ── Top-level entry point ─────────────────────────────────────────────


_RULES = (
    _rule_robots_critical,
    _rule_indexation_coverage,
    _rule_harmful_visibility,
    _rule_ctr_gap,
    _rule_wordstat_partial_coverage,
    _rule_missing_landings,
    _rule_pages_without_review,
    _rule_pending_recs,
    _rule_followup_due,
)


def build_plan(
    snap: BrainSnapshot,
    *,
    max_actions: int = 5,
    target_config: dict | None = None,
) -> Plan:
    """Apply each rule, drop None, sort by (focus, severity), cap at N.

    `target_config` is optional for backwards compat (existing tests
    that build a snapshot directly don't need to pass it). When set,
    we pull strategic_focus from it and feed each rule a list of
    focus tokens — every Action thus knows whether it's in-focus,
    and the sort puts in-focus actions first.
    """
    from app.core_audit.strategic_focus import from_target_config

    focus = from_target_config(target_config or {})
    focus_tokens = _focus_tokens(focus)

    actions: list[Action] = []
    for rule in _RULES:
        a = rule(snap, focus_tokens)
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
        focus_label=focus.label if focus else None,
    )


__all__ = ["Action", "Plan", "Severity", "build_plan"]
