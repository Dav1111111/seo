"""Markdown renderer for WeeklyReport — GitHub-flavored, Russian."""

from __future__ import annotations

from app.core_audit.report.dto import WeeklyReport


def render_markdown(report: WeeklyReport) -> str:
    out: list[str] = []
    m = report.meta
    out.append(f"# Еженедельный SEO-отчёт — {m.site_host}")
    out.append(f"_{m.week_start} — {m.week_end} (UTC)_  ·  "
               f"сформирован {m.generated_at.strftime('%Y-%m-%d %H:%M')}  ·  "
               f"статус: {m.status}")
    out.append("")

    # 0. Diagnostic (Phase E) — rendered only when available.
    d = report.diagnostic
    if d.available:
        out.append("# 🧭 Корневая проблема")
        out.append(d.root_problem_ru)
        out.append("")
        if d.supporting_symptoms_ru:
            out.append("**Сопутствующие симптомы:**")
            for s in d.supporting_symptoms_ru:
                out.append(f"- {s}")
            out.append("")
        if d.recommended_first_actions_ru:
            out.append("**Что делать в первую очередь:**")
            for i, a in enumerate(d.recommended_first_actions_ru, 1):
                out.append(f"{i}. {a}")
            out.append("")

        bd = d.brand_demand or {}
        nbd = d.non_brand_demand or {}
        out.append("**Спрос**")
        out.append(
            f"- Брендовый: {bd.get('clusters', 0)} кластеров, "
            f"{bd.get('observed_impressions', 0)} показов"
        )
        out.append(
            f"- Небрендовый: {nbd.get('clusters', 0)} кластеров, "
            f"{nbd.get('observed_impressions', 0)} показов, "
            f"{nbd.get('covered', 0)}/{nbd.get('clusters', 0)} покрыты"
        )
        out.append("")

        if d.missing_target_clusters:
            out.append("**Приоритетные пробелы (топ-10):**")
            for c in d.missing_target_clusters:
                cov = (
                    f"{c.coverage_score:.2f}" if c.coverage_score is not None else "—"
                )
                out.append(
                    f"- {c.name_ru} — релевантность {c.business_relevance:.2f}, "
                    f"покрытие {cov}"
                )
            out.append("")

        if d.low_priority_findings:
            out.append("**Понижено в приоритете (было топом в старой выдаче):**")
            for f_ in d.low_priority_findings:
                out.append(f"- {f_}")
            out.append("")

    # 1. Executive
    e = report.executive
    out.append(f"## 1. Резюме  ·  Health Score: **{e.health_score}/100**")
    if e.wow_impressions_pct is not None:
        out.append(f"**Показы WoW:** {e.wow_impressions_pct:+.1f}%  ·  "
                   f"**Клики WoW:** {(e.wow_clicks_pct or 0):+.1f}%")
    out.append("")
    out.append(e.prose_ru)
    out.append("")
    if e.top_wins:
        out.append("**Победы недели:**")
        for w in e.top_wins:
            out.append(f"- ✅ {w}")
    if e.top_losses:
        out.append("")
        out.append("**Потери недели:**")
        for l in e.top_losses:
            out.append(f"- ⚠️ {l}")
    out.append("")

    # 2. Action Plan
    ap = report.action_plan
    out.append(f"## 2. План на эту неделю  ·  топ-{len(ap.items)} из {ap.total_in_backlog}")
    out.append(ap.narrative_ru or "")
    out.append("")
    if ap.items:
        out.append("| # | Приоритет | Категория | Владелец | ETA | Задача |")
        out.append("|---|---|---|---|---|---|")
        for i, it in enumerate(ap.items, 1):
            out.append(
                f"| {i} | {it.priority} ({it.priority_score:.1f}) | "
                f"{it.category} | {it.suggested_owner} | {it.eta_ru} | "
                f"{it.page_url or '—'}: {it.reasoning_ru[:100]}… |"
            )
    else:
        out.append("_Нет приоритетных задач — запустите ревью страниц._")
    out.append("")

    # 3. Coverage
    c = report.coverage
    out.append(f"## 3. Покрытие интентов  ·  сильных: {c.strong_count} / слабых: "
               f"{c.weak_count} / отсутствуют: {c.missing_count}")
    out.append(f"Открытых решений в очереди: **{c.open_decisions_count}**")
    if c.intent_gaps:
        out.append("\n**Пробелы:**")
        for g in c.intent_gaps:
            out.append(f"- {g}")
    out.append("")

    # 4. Trends
    t = report.query_trends
    if not t.data_available:
        out.append(f"## 4. Тренды запросов\n_{t.note_ru or 'Нет данных'}_\n")
    else:
        imp_this = t.totals_this_week.get("impressions", 0)
        imp_prev = t.totals_prev_week.get("impressions", 0)
        out.append(f"## 4. Тренды запросов  ·  показы: {imp_this:,} (+{t.wow_diff.get('impressions_pct', 0):.1f}% WoW от {imp_prev:,})")
        if t.top_movers_up:
            out.append("\n**Растут (топ-5):**")
            for m_ in t.top_movers_up[:5]:
                out.append(f"- `{m_.query_text}`: +{m_.impressions_diff} показов")
        if t.top_movers_down:
            out.append("\n**Падают (топ-5):**")
            for m_ in t.top_movers_down[:5]:
                out.append(f"- `{m_.query_text}`: {m_.impressions_diff} показов")
        if t.new_queries:
            out.append(f"\n**Новые запросы в топ-50:** {', '.join(t.new_queries)}")
        if t.lost_queries:
            out.append(f"\n**Потеряны из топ-50:** {', '.join(t.lost_queries)}")
        out.append("")

    # 5. Page Findings
    f = report.page_findings
    out.append(f"## 5. Ревью страниц  ·  страниц: {f.pages_reviewed}, "
               f"ревью: {f.reviews_run_count}")
    if f.warning_ru:
        out.append(f"_{f.warning_ru}_\n")
    else:
        if f.by_priority_count:
            order = ["critical", "high", "medium", "low"]
            counts = " · ".join(f"{p}: {f.by_priority_count.get(p, 0)}" for p in order)
            out.append(f"**По приоритету:** {counts}")
        out.append("")
        for p in f.pages[:10]:
            out.append(f"### {p.page_url or p.page_id}")
            out.append(f"интент: {p.target_intent_code}  ·  "
                       f"крит: {p.critical_count} / важно: {p.high_count} / "
                       f"средне: {p.medium_count}")
            if p.top_issues:
                out.append(f"- Основные проблемы: {', '.join(p.top_issues)}")
            if p.missing_eeat_signals:
                out.append(f"- Не найдено E-E-A-T: {', '.join(p.missing_eeat_signals)}")
            if p.missing_commercial_factors:
                out.append(f"- Не найдено комм. факторов: {', '.join(p.missing_commercial_factors)}")
            out.append("")

    # 6. Technical
    tech = report.technical
    out.append(f"## 6. Техническое SEO  ·  индексация: "
               f"{tech.indexation_rate * 100:.1f}% ({tech.pages_indexed}/{tech.pages_total})")
    out.append(f"- Non-200 страниц: {tech.pages_non_200}")
    out.append(f"- Подозрение на дубли: {tech.duplicates_suspected}")
    out.append(f"- Устаревшие fingerprints (>30 дней): {tech.fingerprint_stale_count}")
    if tech.warning_ru:
        out.append(f"\n_⚠ {tech.warning_ru}_")

    out.append("")
    out.append(f"---\n_v{m.builder_version}  ·  LLM: ${m.llm_cost_usd:.4f}  ·  "
               f"{m.generation_ms}ms_")
    return "\n".join(out)
