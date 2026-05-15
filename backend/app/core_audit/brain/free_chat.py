"""Free chat — Phase C + E.

Separate from `brain.chat` (which is per-action). Owner asks anything
about the site — «почему Webmaster такое показывает», «что такое
индексация», «с какого тура начать», «что мне приоритетнее всего».

The LLM gets a much wider context than per-action chat:
  - business understanding (narrative + observed facts)
  - target_config (primary product, services, regions)
  - the full BrainSnapshot (indexation, queries, reviews, competitors, outcomes)
  - the full plan with all actions in it (so the LLM knows what
    has already been recommended; it can REFER to plan items but
    must NOT invent new ones)
  - the conversation history

Hard rules (system prompt enforces): no fabrication, only data shown
in CONTEXT, refer to the plan / module instead of inventing answers,
trust owner overrides, plain Russian.
"""

from __future__ import annotations

from typing import Any

from app.agents.llm_client import call_plain, call_with_optional_tools
from app.core_audit.brain.rules import Plan
from app.core_audit.brain.snapshot import (
    REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT,
    BrainSnapshot,
)


# ── Constants ────────────────────────────────────────────────────────


MAX_HISTORY_MESSAGES = 16
MAX_REPLY_TOKENS = 1500
MAX_DISCUSSION_REPLY_TOKENS = 2200
MAX_BATTLE_PLAN_REPLY_TOKENS = 3200
MAX_USER_MESSAGE_CHARS = 2000
NARRATIVE_TRIM = 1500          # trim narrative_ru at this many chars
FACTS_LIMIT = 8                # how many observed_facts to include
HARMFUL_EXAMPLES_LIMIT = 8     # spam / disputed query examples
URL_EXAMPLES_LIMIT = 5         # not-indexed / unreviewed URL samples
CHAT_MODES = {"answer", "discussion", "battle_plan"}


# ── System prompt ────────────────────────────────────────────────────


# ── Tool: propose_strategic_focus ──────────────────────────────────


PROPOSE_FOCUS_TOOL = {
    "name": "propose_strategic_focus",
    "description": (
        "Предложи владельцу установить новый стратегический фокус "
        "сайта. Используй ТОЛЬКО когда владелец явно говорит, что "
        "хочет на чём-то сосредоточиться, или когда он несколько раз "
        "упоминает одно направление как главное. Не используй для "
        "вопросов «что мне делать» — на них отвечай текстом, ссылаясь "
        "на текущий план. Все списки заполняй на основе того, что "
        "владелец сам сказал в чате — не выдумывай продукты или "
        "регионы из ничего. Если деталей не хватает — заполни label "
        "и products/regions, остальное оставь пустым."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "Одна строка, как владелец сказал. Например "
                    "«Багги-экспедиции в Абхазию»."
                ),
            },
            "products": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Продукты в фокусе. Берёшь из слов владельца "
                    "(«багги», «экспедиции»)."
                ),
            },
            "regions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Регионы в фокусе.",
            },
            "query_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2-5 ключевых запросов, по которым видно, что "
                    "сайт в зоне фокуса. Если непонятно — пустой список."
                ),
            },
            "deprioritised": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Что владелец явно отложил. Только если он сам "
                    "это назвал."
                ),
            },
            "exit_criterion": {
                "type": "string",
                "description": (
                    "Условие выхода из фокуса, если владелец его назвал. "
                    "Иначе пусто."
                ),
            },
            "owner_note": {
                "type": "string",
                "description": (
                    "Свободная заметка от владельца, цитатой или "
                    "близко к тексту."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Короткое объяснение для владельца, почему "
                    "система предлагает именно такой фокус. 1-2 "
                    "предложения. Будет показано в модалке "
                    "подтверждения."
                ),
            },
        },
        "required": ["label", "rationale"],
    },
}


SYSTEM_PROMPT = """\
Ты — внутренний помощник в SEO-инструменте «Yandex Growth Tower».
Владелец сайта может задать тебе любой вопрос про свой сайт. Твоя
задача — отвечать опираясь ТОЛЬКО на данные в блоке КОНТЕКСТ ниже.

Тон — представь, что ты объясняешь дяде Васе, который купил сайт
вчера и не понимает разницу между «Яндексом» и «Webmaster»:

  - Только русский. На «ты». Никаких формальностей.
  - Каждое предложение — короткое. Лучше 2 коротких, чем 1 сложное.
  - Никаких маркетинговых фраз («Отличный вопрос!», «Великолепно!»,
    «Уникальная возможность»). Никаких эмодзи в начале. Только дело.
  - Никаких ИИ-штампов: «давайте рассмотрим», «вот что я предлагаю»,
    «как опытный SEO-специалист я считаю». Просто говори с человеком.
  - Не пиши длинных портянок. Обычный ответ — 3-6 коротких пунктов
    или абзац в 4-6 предложений. Если просят «коротко» — 2-3 строки.
  - Сначала суть в 1 предложении («у тебя 21 страница в индексе из
    24 — это нормально для нового сайта»), потом цифры/детали, потом
    что делать.
  - Каждый совет = простая инструкция: «открой N, нажми Y, увидишь Z».
    Не «следует рассмотреть оптимизацию» — а «зайди в Запросы и
    переключи 5 запросов из спама в свои».

Объяснение терминов — обязательно. Когда в первый раз в ответе
упоминаешь технический термин, дай скобку с переводом на простой:

  - «индексация (Яндекс знает что эта страница есть)»
  - «sitemap.xml (список страниц для поисковика — типа оглавления книги)»
  - «канонический URL (главный адрес страницы; если у одной страницы
    два адреса, это говорит «вот настоящий»)»
  - «E-E-A-T (доверие к сайту — лицензии, ИНН, реальные отзывы)»
  - «Webmaster (бесплатный кабинет Яндекса для владельцев сайтов)»
  - «Wordstat (статистика что люди ищут в Яндексе)»
  - «вредная видимость (тебя находят по запросам не про твою тему —
    типа сайт про экскурсии показывают по «джинсы»)»
  - «CTR (сколько % людей кликают увидев тебя в выдаче)»
  - «снпет / сниппет (то что показывает Яндекс под ссылкой —
    title и описание)»
  - «meta description (короткое описание под title в выдаче)»
  - «schema / разметка (невидимый код страницы который объясняет
    Яндексу что это: тур, цена, отзыв)»

Когда уместно — давай житейские аналогии:
  - «Яндекс не понимает кто ты» = «как если бы тебя по ошибке
    приняли за магазин джинсов»
  - «Поведенческие факторы» = «Яндекс смотрит — кликают на тебя в
    выдаче? задерживаются? возвращаются назад? Если да — продвигает»
  - «Карта спроса» = «таблица: сколько раз люди ищут каждую тему,
    сколько у нас таких страниц, и где дыры»

Не пиши «оптимизация», «семантическое ядро», «SEO-показатели» без
объяснения. Если без термина не обойтись — переведи в скобках сразу.

Жёсткие правила:

  1. НЕ ВЫДУМЫВАЙ. Все факты — только из КОНТЕКСТА. Никаких чисел,
     URL, запросов, услуг или дат, которых там нет. Если вопрос
     не покрыт данными — скажи простым языком: «У меня этого в
     данных нет. Чтобы я это увидел — открой [модуль X] и нажми
     [кнопку Y]».

  2. ССЫЛАЙСЯ НА ПЛАН. Если владелец спрашивает «что мне делать»,
     «с чего начать», «что приоритетнее» — отсылай к УЖЕ ВЫДАННЫМ
     действиям из секции ПЛАН в КОНТЕКСТЕ, по их title. Не создавай
     новые рекомендации помимо плана.

  3. ОБЪЯСНЯЙ ТЕРМИНЫ В МОМЕНТЕ. Видишь что использовал термин — в
     той же фразе дай скобку с переводом. Не отдельным абзацем
     «давайте я объясню что такое...», а прямо в тексте.

  4. УВАЖАЙ ВЛАДЕЛЬЦА. Если он говорит «нет, "прокат сочи" — это мой
     запрос», поверь: «понял, тогда поправь руками — открой запрос и
     отметь как мой». Не спорь и не настаивай на классификации.

  5. ЦИТИРУЙ КОНКРЕТНОЕ. «"джинсы багги"» — а не «один из спам-запросов».
     URL — полный, не сокращённый.

  6. КОГДА ОБСУЖДАЕМ ЯНДЕКС / WEBMASTER. Опирайся на реальные данные:
     если в КОНТЕКСТЕ написано «исключено: 0», то говорить «Яндекс
     наверняка многое исключил» нельзя — это противоречит данным.

  7. КОГДА ОБСУЖДАЕМ ИНДЕКСАЦИЮ. Разделяй три уровня:
     «точно видно» — только подтверждённые статусы Webmaster и
     найденные технические признаки; «может мешать» — noindex,
     non-200, sitemap, canonical, тонкий текст, отсутствие title/H1
     и только если они есть в КОНТЕКСТЕ; «надо проверить» — когда
     статус неизвестен или данных Webmaster нет. Не называй
     «статус неизвестен» страницами, которые точно не в индексе.

  8. КОГДА НЕ ЗНАЕШЬ — скажи это прямо. «В данных нет ответа на этот
     вопрос. Проверить можно в [модуль]» — нормальный ответ. Лучше
     честное «не знаю», чем правдоподобная выдумка.

  9. РЕЖИМ ОБСУЖДЕНИЯ. Если в КОНТЕКСТЕ указан режим «обсуждение»,
     не закрывай тему сухим финальным ответом. Помоги владельцу
     подумать: коротко назови факты, покажи 2-3 реальные развилки,
     выбери следующий разумный ход по данным и задай максимум два
     точных вопроса, если без них решение будет слабым. Если данных
     уже достаточно — не задавай вопросов ради вопросов.

 10. КОГДА ОБСУЖДАЕМ КОНКУРЕНТОВ. Разделяй три слоя: SERP-разведка
     показывает домены, которые реально попались в выдаче по нашим
     запросам; deep-dive показывает только SEO-признаки страниц, которые
     система реально открыла и распарсила; opportunities — это выводы
     системы из gap/diff. Если какого-то слоя нет в КОНТЕКСТЕ — прямо
     скажи, что он не запускался или данных нет. Не называй конкурентом
     домен, которого нет в блоке «Конкуренты».

 11. ГРУППЫ ПОВТОРЯЮЩИХСЯ ПРОБЛЕМ. В КОНТЕКСТЕ есть блок «группы
     повторяющихся проблем» — это та же бесконечная простыня
     рекомендаций, но свёрнутая по теме («title слишком длинный
     ×12 страниц»). Когда владелец спрашивает «что мне чинить?»
     или «какие у меня повторяющиеся проблемы?» — отвечай ИМЕННО
     группами, не перечисляя каждую страницу:
       — «У тебя 4 повторяющиеся темы: title (12 стр.), Schema
          (24 стр.), E-E-A-T (32 стр.), commercial (16 стр.).»
       — Спроси «обсуждаем все по очереди или начнём с самой
          крупной?» вместо того чтобы вываливать 84 пункта.
     Когда владелец говорит «эту тему пропустим / не интересно» —
     запомни в текущем разговоре и больше её не предлагай. Когда
     просит «развернуть конкретную страницу из группы» — переключайся
     на плоский список рекомендаций (он ниже в контексте).

 12. СВЕЖИЕ СНИМКИ ПОСЛЕ РЕВЬЮ. Если у рекомендации указано
     «свежий снимок после ревью», НЕ говори её как точный текущий
     дефект. Формулируй честно: «ревью старое, после него есть свежий
     браузерный снимок; по свежему снимку видно ...; надо перезапустить
     ревью/проверку, чтобы закрыть рекомендацию». Если свежий снимок
     показывает, что поле уже появилось, скажи «похоже, это уже
     починили», а не повторяй старую проблему.

 13. ВНУТРЕННИЕ КОДЫ. В КОНТЕКСТЕ могут встречаться технические
     идентификаторы (`source_finding_id`, `signal=X`, finding-ID,
     rec_id и подобные строки с подчёркиваниями/точками вида
     `commercial.missing_phone_in_header`). Это для тебя ориентир —
     какая Python-проверка нашла проблему. **Не цитируй эти ID
     владельцу и не показывай их в ответе.** Опиши проблему
     человеческими словами на русском: «не нашёл телефон в шапке»,
     а не «source_finding_id=commercial.missing_phone_in_header».

 14. РЕЖИМ БОЕВОГО SEO-ПЛАНА. Если в КОНТЕКСТЕ указан режим
     «боевой план», собери план под цель топ-5, но не обещай топ-5.
     Максимум 5 действий. Каждое действие должно иметь: страница или
     модуль, почему это важно по фактам, что сделать, ожидаемый эффект
     без числовых обещаний, как проверить результат и когда вернуться
     к проверке. Сначала закрывай технические блокеры индексации, потом
     high/critical рекомендации по страницам, потом конкурентные
     разрывы и запросы. Если конкуренты или метрики не запускались —
     прямо скажи, чего не хватает.

     ВАЖНО про блок «ОСНОВА БОЕВОГО ПЛАНА». Если он есть в КОНТЕКСТЕ —
     это фактологический план, сгенерированный системой детерминистски
     из снапшота. Все факты, страницы, рекомендации в нём реальны.
     Твоя задача — НЕ переписать план с нуля, а АДАПТИРОВАТЬ его под
     конкретный вопрос владельца:
       — «коротко в 3 пункта» → сократи seed до 3 самых сильных, не
          теряя главного
       — «только по индексации» → оставь только indexation-блоки seed
       — «без E-E-A-T» / «потом» → исключи действия про РТО/ИНН/ОГРН
       — «разверни punkt 2» → возьми один пункт seed и распиши детальнее
       — «другими словами» → перефразируй seed, факты те же
     Никогда не добавляй новых пунктов или цифр, которых нет в seed
     или в КОНТЕКСТЕ. Никогда не противоречь seed (если в seed написано
     «12 страниц с проблемой», не пиши «10 страниц»). Если запрос
     владельца НЕ совместим с фактами (просит «оптимизация под бренд»,
     а в seed нет ничего про бренд) — честно скажи «таких данных не
     вижу, давай по тому что есть».

 15. ДАННЫЕ МЕТРИКИ. Если в КОНТЕКСТЕ в блоке «Метрика» указано
     «предупреждение: код Метрики на сайте не отвечает» (любой
     статус кроме CS_OK — например CS_ERR_UNKNOWN,
     CS_ERR_NOT_INSTALLED), то ВСЕ цифры из блока Метрики — визиты,
     просмотры, посадочные, источники, конверсии — недостоверны.
     Не ссылайся на них как на факты. Не делай выводов про отказы,
     поведение, конверсии. Скажи владельцу честно: «по данным
     Метрики ничего сказать не могу — счётчик показывает что код не
     установлен / не отвечает на сайте; первое действие — проверь
     установку кода Метрики, потом запусти сбор заново». Это
     обязательно: лучше «не знаю, починим счётчик» чем выдуманный
     анализ нулевых данных.

Запрещено:
  - Гарантировать рост позиций / трафика. Только «вероятно по данным».
  - Давать общие SEO-советы из обучения, не привязанные к КОНТЕКСТУ.
  - Писать длинные планы или «вот тебе 10 шагов» сверх плана из
    КОНТЕКСТА. Исключение — режим «боевой план»: там максимум 5
    действий, только из КОНТЕКСТА.

ИНСТРУМЕНТ propose_strategic_focus:
  Тебе дан инструмент propose_strategic_focus. Вызывай его ТОЛЬКО
  когда владелец явно просит сменить или установить фокус — например:
    «давай сосредоточимся на X», «сейчас для меня важно только Y»,
    «можешь скорректировать вводные про проект, чтобы …»,
    «фокус: …», «приоритет — …».
  Если владелец просто спрашивает «что делать», «с чего начать»,
  «расскажи про…» — НЕ вызывай инструмент. Отвечай текстом, ссылайся
  на ТЕКУЩИЙ ПЛАН.
  Когда вызываешь инструмент — заполняй поля ТОЛЬКО на основе слов
  владельца. Не выдумывай продукты, регионы, exit_criterion — если
  владелец не назвал, оставляй пусто.
  В rationale напиши 1-2 предложения, почему ты предложил именно
  это — это покажется в модалке подтверждения.
  Сам ничего не записывает — после вызова инструмента владелец
  увидит модалку «Применить фокус?» и подтвердит вручную.
"""


# ── Context builders ─────────────────────────────────────────────────


def _format_business_block(
    *, domain: str, target_config: dict[str, Any], understanding: dict[str, Any],
) -> str:
    parts: list[str] = [f"САЙТ: {domain}"]

    primary_product = (target_config or {}).get("primary_product")
    services = (target_config or {}).get("services") or []
    secondary = (target_config or {}).get("secondary_products") or []
    geo_primary = (target_config or {}).get("geo_primary") or []
    geo_secondary = (target_config or {}).get("geo_secondary") or []
    if primary_product:
        parts.append(f"  основной продукт: {primary_product}")
    if isinstance(services, list) and services:
        parts.append(f"  услуги: {', '.join(map(str, services))}")
    if isinstance(secondary, list) and secondary:
        parts.append(
            f"  дополнительные продукты: {', '.join(map(str, secondary))}",
        )
    if isinstance(geo_primary, list) and geo_primary:
        parts.append(
            f"  основные регионы: {', '.join(map(str, geo_primary))}",
        )
    if isinstance(geo_secondary, list) and geo_secondary:
        parts.append(
            f"  второстепенные регионы: {', '.join(map(str, geo_secondary))}",
        )

    narrative = (understanding or {}).get("narrative_ru") or ""
    narrative = narrative.strip()
    if narrative:
        if len(narrative) > NARRATIVE_TRIM:
            narrative = narrative[:NARRATIVE_TRIM] + " […]"
        parts.append("")
        parts.append("ОПИСАНИЕ БИЗНЕСА:")
        parts.append(narrative)

    facts = (understanding or {}).get("observed_facts") or []
    if isinstance(facts, list) and facts:
        rendered: list[str] = []
        for f in facts[:FACTS_LIMIT]:
            if isinstance(f, dict):
                txt = (f.get("fact") or "").strip()
                if not txt:
                    continue
                ref = (f.get("page_ref") or "").strip()
                rendered.append(f"  - {txt}" + (f"  [{ref}]" if ref else ""))
            elif isinstance(f, str):
                rendered.append(f"  - {f.strip()}")
        if rendered:
            parts.append("")
            parts.append(
                "ЧТО МЫ САМИ УВИДЕЛИ НА САЙТЕ (объективные факты):",
            )
            parts.extend(rendered)

    return "\n".join(parts)


def _format_full_snapshot(snap: BrainSnapshot) -> str:
    """Whole snapshot — all 5 sections — in compact bullet form. The
    free chat doesn't slice; the LLM may need any of these to answer
    a free-form question."""
    parts: list[str] = ["СОСТОЯНИЕ САЙТА (на момент запроса):"]

    # Indexation
    idx = snap.indexation
    parts.append("  Индексация:")
    parts.append(f"    всего страниц: {idx.pages_total}")
    parts.append(f"    в индексе Яндекса: {idx.pages_in_index}")
    parts.append(f"    исключено: {idx.pages_excluded}")
    parts.append(f"    статус неизвестен: {idx.pages_unknown}")
    parts.append(f"    страниц с проверенным статусом Webmaster: {idx.checked_pages}")
    if idx.last_checked_at:
        parts.append(f"    последняя проверка URL в Webmaster: {_format_dt(idx.last_checked_at)}")
    if idx.coverage_pct is not None:
        parts.append(
            f"    доля страниц в индексе от всех найденных страниц: "
            f"{idx.coverage_pct:.1f}%",
        )
    parts.append(
        "    правило интерпретации: «статус неизвестен» не означает "
        "«страница точно не в индексе»",
    )
    if idx.latest_indexing_date or idx.latest_pages_indexed_metric is not None:
        line = "    последняя метрика Webmaster / indexing:"
        if idx.latest_indexing_date:
            line += f" дата {_format_date(idx.latest_indexing_date)}"
        if idx.latest_pages_indexed_metric is not None:
            line += f", страниц в индексе {idx.latest_pages_indexed_metric}"
        extra = _format_extra(idx.latest_indexing_extra)
        if extra:
            line += f", детали: {extra}"
        parts.append(line)
    if idx.latest_search_events_date or idx.latest_pages_in_search_metric is not None:
        line = "    последняя метрика Webmaster / search_events:"
        if idx.latest_search_events_date:
            line += f" дата {_format_date(idx.latest_search_events_date)}"
        if idx.latest_pages_in_search_metric is not None:
            line += f", страниц в поиске {idx.latest_pages_in_search_metric}"
        extra = _format_extra(idx.latest_search_events_extra)
        if extra:
            line += f", детали: {extra}"
        parts.append(line)
    parts.append("    технические признаки, которые могут мешать индексации:")
    parts.append(f"      - non-200 страницы: {idx.non_200_count}")
    parts.append(f"      - meta robots noindex: {idx.noindex_count}")
    parts.append(f"      - нет в sitemap: {idx.not_in_sitemap_count}")
    parts.append(f"      - canonical отсутствует: {idx.canonical_missing_count}")
    parts.append(f"      - canonical на другой домен: {idx.canonical_external_count}")
    parts.append(f"      - canonical не совпадает с URL: {idx.canonical_mismatch_count}")
    parts.append(f"      - мало текста (<200 слов): {idx.low_word_count_count}")
    parts.append(f"      - нет title: {idx.missing_title_count}")
    parts.append(f"      - нет H1: {idx.missing_h1_count}")
    if idx.sample_not_indexed_urls:
        parts.append("    примеры не в индексе:")
        for u in idx.sample_not_indexed_urls[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")
    if idx.sample_excluded:
        parts.append("    примеры исключённых:")
        for ex in idx.sample_excluded[:URL_EXAMPLES_LIMIT]:
            url = ex.get("url", "")
            reason = ex.get("reason", "")
            parts.append(f"      - {url} (причина: {reason or '—'})")
    if idx.sample_non_200:
        parts.append("    примеры non-200:")
        for item in idx.sample_non_200[:URL_EXAMPLES_LIMIT]:
            parts.append(
                f"      - {item.get('url', '')} "
                f"(HTTP {item.get('http_status', '—')})",
            )
    if idx.sample_noindex:
        parts.append("    примеры noindex:")
        for u in idx.sample_noindex[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")
    if idx.sample_not_in_sitemap:
        parts.append("    примеры не в sitemap:")
        for u in idx.sample_not_in_sitemap[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")
    if idx.sample_canonical_issues:
        parts.append("    примеры проблем canonical:")
        for item in idx.sample_canonical_issues[:URL_EXAMPLES_LIMIT]:
            kind = item.get("kind", "")
            canonical = item.get("canonical", "")
            parts.append(
                f"      - {item.get('url', '')} "
                f"({kind}, canonical: {canonical or '—'})",
            )
    if idx.sample_low_word_count:
        parts.append("    примеры страниц с малым текстом:")
        for item in idx.sample_low_word_count[:URL_EXAMPLES_LIMIT]:
            parts.append(
                f"      - {item.get('url', '')} "
                f"({item.get('word_count', 0)} слов)",
            )

    # Queries
    q = snap.queries
    parts.append("  Запросы:")
    parts.append(f"    всего: {q.total}")
    parts.append(
        f"    мои: {q.own}, смежные: {q.adjacent}, "
        f"спорные: {q.disputed}, спам: {q.spam}, "
        f"не разобраны: {q.unclassified}",
    )
    if q.with_volume:
        parts.append(f"    с известным объёмом Wordstat: {q.with_volume}")
    if q.sample_own:
        parts.append("    примеры «моих»:")
        for w in q.sample_own[:5]:
            parts.append(f"      - «{w}»")
    if q.sample_harmful:
        parts.append("    примеры вредных:")
        for h in q.sample_harmful[:HARMFUL_EXAMPLES_LIMIT]:
            qt = h.get("query_text", "") if isinstance(h, dict) else str(h)
            rel = h.get("relevance", "") if isinstance(h, dict) else ""
            reason = h.get("reason_ru", "") if isinstance(h, dict) else ""
            line = f"      - «{qt}» [{rel}]"
            if reason:
                line += f" — {reason}"
            parts.append(line)

    # Competitors
    c = snap.competitors
    parts.append("  Конкуренты:")
    if c.domains:
        parts.append(
            f"    сохранённые домены: {len(c.domains)} — "
            f"{', '.join(c.domains[:10])}",
        )
    else:
        parts.append("    сохранённые домены: 0")
    if c.profile_available:
        parts.append(
            "    SERP-разведка: "
            f"проверено запросов {c.queries_probed}, "
            f"с результатами {c.queries_with_results}, "
            f"уникальных внешних доменов {c.unique_domains_seen}, "
            f"примерная стоимость ${c.cost_usd:.4f}",
        )
        if c.profile_computed_at:
            staleness = (
                " (УСТАРЕЛО, нужна повторная разведка)"
                if c.profile_is_stale else ""
            )
            parts.append(
                f"    разведано: {c.profile_computed_at.strftime('%Y-%m-%d')}"
                f", {c.profile_stale_days or 0} дн. назад"
                f"{staleness}",
            )
        if c.errors:
            parts.append(f"    ошибки SERP-разведки: {_format_extra(c.errors)}")
    else:
        parts.append("    SERP-разведка: данных нет")
    if c.top_competitors:
        parts.append("    кто реально виден в выдаче:")
        for comp in c.top_competitors:
            line = (
                f"      - {comp.get('domain', '')}: "
                f"попаданий {comp.get('serp_hits', 0)}, "
                f"лучшая позиция {comp.get('best_position', 0)}, "
                f"средняя {comp.get('avg_position', 0)}"
            )
            if comp.get("example_query"):
                line += f"; запрос «{comp.get('example_query')}»"
            if comp.get("example_title"):
                line += f"; title «{comp.get('example_title')}»"
            if comp.get("example_url"):
                line += f"; URL {comp.get('example_url')}"
            parts.append(line)
    if c.deep_dive_available:
        parts.append("    deep-dive по SEO-признакам:")
        if c.self_signals:
            own_line = (
                f"      - наш сайт: {_format_competitor_flags(c.self_signals)}"
            )
            if c.self_signals.get("title"):
                own_line += f"; title «{c.self_signals.get('title')}»"
            parts.append(own_line)
        for comp in c.deep_dive_competitors:
            line = (
                f"      - {comp.get('domain', '')}: "
                f"{_format_competitor_flags(comp)}"
            )
            schemas = comp.get("schema_types") or []
            if schemas:
                line += f"; schema {', '.join(map(str, schemas[:8]))}"
            parts.append(line)
            for page in (comp.get("pages") or [])[:1]:
                page_line = f"        страница: {page.get('url', '')}"
                if page.get("status"):
                    page_line += f" [{page.get('status')}]"
                if page.get("title"):
                    page_line += f"; title «{page.get('title')}»"
                if page.get("h1"):
                    page_line += f"; H1 «{page.get('h1')}»"
                if page.get("word_count"):
                    page_line += f"; {page.get('word_count')} слов"
                parts.append(page_line)
    else:
        parts.append("    deep-dive по SEO-признакам: данных нет")
    if c.growth_opportunities:
        parts.append("    возможности роста из анализа конкурентов:")
        for idx, opp in enumerate(c.growth_opportunities[:12], start=1):
            line = (
                f"      {idx}. [{opp.get('priority')}/"
                f"{opp.get('source')}/{opp.get('category')}] "
                f"{opp.get('title_ru', '')}"
            )
            reason = (opp.get("reasoning_ru") or "").strip()
            if reason:
                line += f" — {reason}"
            evidence = _format_competitor_evidence(opp.get("evidence"))
            if evidence:
                line += f" | факт: {evidence}"
            parts.append(line)
    else:
        parts.append("    возможности роста из анализа конкурентов: данных нет")

    # Review
    r = snap.review
    parts.append("  Ревью страниц:")
    parts.append(f"    с ревью: {r.pages_with_review}")
    parts.append(f"    без ревью: {r.pages_without_review}")
    parts.append(
        f"    рекомендаций ждут решения: {r.recs_pending} "
        f"(из них высокого приоритета: {r.recs_high_priority_pending})",
    )
    if getattr(r, "recs_with_fresh_snapshot_after_review", 0):
        parts.append(
            "    переданных рекомендаций со свежим браузерным снимком после ревью: "
            f"{r.recs_with_fresh_snapshot_after_review}; их нельзя "
            "называть текущими проблемами без повторного ревью",
        )
    parts.append(
        "    источник рекомендаций: последние завершённые ревью по "
        "каждой паре страница+интент; это тот же текущий слой, "
        "который использует /studio/pages",
    )
    if r.sample_unreviewed_urls:
        parts.append("    примеры без ревью:")
        for u in r.sample_unreviewed_urls[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")
    # Grouped view first — surfaces "this same problem on N pages"
    # so the assistant can talk about TOPICS instead of repeating
    # identical reasoning verbatim. The flat list still follows for
    # cases where the assistant needs a specific URL or rec_id.
    groups = getattr(r, "recommendation_groups", None) or []
    if groups:
        parts.append(
            f"    группы повторяющихся проблем: {len(groups)} "
            f"(одна тема = одна группа, count = страниц с этой проблемой)",
        )
        for idx, g in enumerate(groups[:20], start=1):
            line = (
                f"      [{g.get('priority')}/{g.get('category')}] "
                f"×{g.get('count')} страниц — "
                f"{(g.get('reasoning_sample') or '')[:120]}"
            )
            urls = g.get("sample_urls") or []
            if urls:
                line += f" | примеры: {', '.join(urls[:3])}"
            fresh_count = int(g.get("fresh_snapshot_after_review_count") or 0)
            if fresh_count:
                line += (
                    f" | свежий снимок после ревью: {fresh_count} "
                    "страниц — перепроверь перед утверждением"
                )
            after = (g.get("after_sample") or "").strip()
            if after:
                line += f" | правка-шаблон: «{after[:120]}»"
            parts.append(line)
        if len(groups) > 20:
            parts.append(
                f"      …ещё {len(groups) - 20} групп (см. /studio/pages)"
            )
    if r.top_pending_recommendations:
        n_in_context = len(r.top_pending_recommendations)
        if n_in_context >= r.recs_pending:
            parts.append(
                f"    все ожидающие рекомендации в контексте: {n_in_context} "
                f"из {r.recs_pending}; это полный список, а не примеры",
            )
        else:
            parts.append(
                f"    рекомендации в контексте: {n_in_context} из "
                f"{r.recs_pending}; переданы первые "
                f"{REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT} по приоритету; "
                "это не полный список",
            )
        parts.append("    плоский список рекомендаций (для ссылок на rec_id):")
        for idx, rec in enumerate(r.top_pending_recommendations, start=1):
            line = (
                f"      {idx}. [{rec.get('priority')}/{rec.get('category')}] "
                f"{rec.get('url', '')}"
            )
            if rec.get("priority_score") is not None:
                line += f" score={rec.get('priority_score')}"
            if rec.get("target_intent_code"):
                line += f" intent={rec.get('target_intent_code')}"
            if rec.get("source_finding_id"):
                line += f" source={rec.get('source_finding_id')}"
            if rec.get("rec_id"):
                line += f" rec_id={rec.get('rec_id')}"
            score_bits = []
            if rec.get("impact_score") is not None:
                score_bits.append(f"impact={rec.get('impact_score')}")
            if rec.get("confidence_score") is not None:
                score_bits.append(f"confidence={rec.get('confidence_score')}")
            if rec.get("ease_score") is not None:
                score_bits.append(f"ease={rec.get('ease_score')}")
            if score_bits:
                line += " " + " ".join(score_bits)
            reason = (rec.get("reasoning_ru") or "").strip()
            if reason:
                line += f" — {reason}"
            before = (rec.get("before_text") or "").strip()
            after = (rec.get("after_text") or "").strip()
            if before:
                line += f" | сейчас: «{before}»"
            if after:
                line += f" | правка: «{after}»"
            fresh = _format_current_snapshot(rec.get("current_snapshot"))
            if fresh:
                line += f" | {fresh}"
            parts.append(line)

    # Missing landings
    m = snap.missing_landings
    parts.append("  Услуги без отдельной страницы:")
    parts.append(
        f"    всего: {m.total} (важных: {m.high_priority}, "
        f"средних: {m.medium_priority}, несрочных: {m.low_priority})",
    )
    if m.computed_at:
        staleness = (
            " (УСТАРЕЛО, нужно пересканировать)" if m.is_stale else ""
        )
        parts.append(
            f"    сканировано: {m.computed_at.strftime('%Y-%m-%d')}"
            f", {m.stale_days or 0} дн. назад{staleness}",
        )
    if m.items:
        parts.append("    примеры:")
        for it in m.items[:5]:
            name = (it.get("service_name") or "").strip()
            prio = it.get("priority", "")
            quote = (it.get("evidence_quote") or "").strip()
            line = f"      - {name} [{prio}]"
            if quote:
                line += f" — цитата из описания: «{quote}»"
            parts.append(line)

    # Outcomes
    o = snap.outcomes
    parts.append("  Применённые правки и замеры:")
    parts.append(f"    всего применено: {o.applied_total}")
    parts.append(f"    за последние 14 дней: {o.applied_last_14d}")
    parts.append(f"    ждут замера через 14 дней: {o.pending_followup}")

    # Metrica
    met = getattr(snap, "metrica", None)
    parts.append("  Метрика / поведение посетителей:")
    if met is None or not met.latest_date:
        parts.append("    данных нет: нельзя делать выводы о визитах, отказах и конверсиях")
    else:
        status_bits = []
        if met.counter_status:
            status_bits.append(f"status={met.counter_status}")
        if met.counter_activity_status:
            status_bits.append(f"activity={met.counter_activity_status}")
        if met.counter_code_status:
            status_bits.append(f"code_status={met.counter_code_status}")
        if status_bits:
            parts.append("    счётчик: " + ", ".join(status_bits))
        # If the counter code isn't reporting back, every other Metrica
        # number is meaningless («0 визитов» = «нет данных», not «нет
        # трафика»). Spell this out explicitly so the LLM doesn't draw
        # behavioral conclusions on garbage data.
        if met.counter_code_status and met.counter_code_status != "CS_OK":
            parts.append(
                "    предупреждение: код Метрики на сайте не отвечает или не "
                "установлен (статус «{status}»). «0 визитов» при таком "
                "статусе означает «нет данных», НЕ «нет трафика». Не "
                "делай выводов про отказы, конверсии, посадочные "
                "страницы — данные недостоверны, пока счётчик не "
                "починят.".format(status=met.counter_code_status)
            )
        parts.append(
            f"    последние 7 дней до {_format_date(met.latest_date)}: "
            f"{met.visits_7d} визитов, {met.pageviews_7d} просмотров"
        )
        if met.avg_bounce_rate is not None or met.avg_duration_sec is not None:
            line = "    поведение:"
            if met.avg_bounce_rate is not None:
                line += f" отказы {_format_decimal_pct(met.avg_bounce_rate)}"
            if met.avg_duration_sec is not None:
                line += f", среднее время {met.avg_duration_sec:.0f} сек."
            parts.append(line)
        if met.visits_7d == 0:
            parts.append(
                "    правило интерпретации: визитов нет — не делай выводы "
                "об отказах, качестве страниц или конверсиях",
            )
        if met.top_landing_pages:
            parts.append("    топ посадочных по Метрике:")
            for item in met.top_landing_pages[:6]:
                line = (
                    f"      - {item.get('url', '')}: "
                    f"{item.get('visits', 0)} визитов, "
                    f"{item.get('pageviews', 0)} просмотров"
                )
                if item.get("bounce_rate") is not None:
                    line += f", отказы {_format_decimal_pct(item.get('bounce_rate'))}"
                if item.get("avg_duration_sec") is not None:
                    line += f", время {float(item.get('avg_duration_sec') or 0):.0f} сек."
                if not item.get("mapped_page_id"):
                    line += " (не сопоставлено с Page)"
                parts.append(line)
        if met.traffic_sources:
            parts.append("    источники трафика:")
            for item in met.traffic_sources[:6]:
                parts.append(
                    f"      - {item.get('source', '')}: "
                    f"{item.get('visits', 0)} визитов, "
                    f"{item.get('pageviews', 0)} просмотров",
                )
        if met.goals:
            parts.append("    цели Метрики:")
            for goal in met.goals[:8]:
                line = (
                    f"      - {goal.get('name') or goal.get('goal_id')}: "
                    f"{goal.get('reaches', 0)} достижений, "
                    f"{goal.get('target_visits', 0)} целевых визитов"
                )
                if goal.get("conversion_rate") is not None:
                    line += f", конверсия {float(goal.get('conversion_rate') or 0):.2f}%"
                parts.append(line)

    # Activity
    a = snap.activity
    parts.append("  Последняя активность системы:")
    if a.latest_pipeline_status:
        line = f"    пайплайн: {a.latest_pipeline_status}"
        if a.latest_pipeline_message:
            line += f" — {a.latest_pipeline_message}"
        if a.latest_pipeline_ts:
            line += f" ({_format_dt(a.latest_pipeline_ts)})"
        parts.append(line)
    else:
        parts.append("    пайплайн: данных нет")
    if a.running_stages:
        parts.append("    сейчас не завершены:")
        for ev in a.running_stages[:5]:
            line = f"      - {ev.get('stage')}: {ev.get('status')}"
            if ev.get("message"):
                line += f" — {ev.get('message')}"
            parts.append(line)
    if a.recent_events:
        parts.append("    последние события:")
        for ev in a.recent_events[:8]:
            line = f"      - {ev.get('stage')}: {ev.get('status')}"
            if ev.get("message"):
                line += f" — {ev.get('message')}"
            if ev.get("ts"):
                line += f" ({ev.get('ts')})"
            parts.append(line)

    return "\n".join(parts)


def _format_mode_block(mode: str) -> str:
    if mode == "battle_plan":
        return "\n".join([
            "РЕЖИМ ОТВЕТА: БОЕВОЙ SEO-ПЛАН",
            "  - Цель: привести владельца к самым сильным действиям для роста к топ-5, без гарантий позиций.",
            "  - Сначала назови 3-5 фактов, на которых строится план.",
            "  - Затем дай максимум 5 действий в порядке выполнения.",
            "  - Для каждого действия обязательно: страница/модуль; причина; что сделать; ожидаемый эффект; как проверить результат.",
            "  - Используй конкретные URL, rec_id, запросы и конкурентов только если они есть в КОНТЕКСТЕ.",
            "  - Если данных для конкурентов, спроса или индексации не хватает — добавь это в блок «что добрать», а не выдумывай.",
        ])
    if mode == "discussion":
        return "\n".join([
            "РЕЖИМ ОТВЕТА: ОБСУЖДЕНИЕ",
            "  - Не просто отвечай, а помоги владельцу разобрать ситуацию.",
            "  - Форматируй мысль как: что видно по фактам; варианты; мой следующий ход; что уточнить.",
            "  - Объясняй причинно-следственную связь: почему факт важен и чем он мешает SEO-решению.",
            "  - Варианты и следующий ход бери из КОНТЕКСТА, плана и списка рекомендаций.",
            "  - Задавай максимум 2 вопроса и только если они реально меняют решение.",
            "  - Если владелец просит выбрать — выбери один вариант и объясни почему по фактам.",
        ])
    return "\n".join([
        "РЕЖИМ ОТВЕТА: РАЗВЁРНУТЫЙ КОРОТКИЙ ОТВЕТ",
        "  - Ответь прямо, но дай достаточно деталей, чтобы владелец понял причину.",
        "  - Структура по умолчанию: что видно по фактам; что это значит; что делать дальше; где проверить.",
        "  - Если есть URL, rec_id, запрос, конкурент или число — процитируй 1-3 конкретных основания из КОНТЕКСТА.",
        "  - Если нужен следующий шаг — ссылайся на текущий план, конкретную рекомендацию или модуль.",
    ])


def _format_current_snapshot(snapshot: Any) -> str:
    if not isinstance(snapshot, dict) or not snapshot:
        return ""
    prefix = (
        "свежий снимок после ревью"
        if snapshot.get("after_review")
        else "последний браузерный снимок"
    )
    bits: list[str] = []
    if snapshot.get("extracted_at"):
        bits.append(str(snapshot.get("extracted_at")))
    if snapshot.get("title"):
        bits.append(f"title сейчас «{str(snapshot.get('title'))[:80]}»")
    if snapshot.get("h1"):
        bits.append(f"H1 сейчас «{str(snapshot.get('h1'))[:80]}»")
    schema_types = snapshot.get("schema_types")
    if isinstance(schema_types, list) and schema_types:
        bits.append("schema сейчас: " + ", ".join(map(str, schema_types[:8])))
    schema_issues = snapshot.get("schema_issue_codes")
    if isinstance(schema_issues, list) and schema_issues:
        bits.append(
            "schema issues: " + ", ".join(map(str, schema_issues[:6])),
        )
    if snapshot.get("lcp_ms"):
        bits.append(f"LCP {snapshot.get('lcp_ms')} мс")
    if snapshot.get("js_error_count") is not None:
        bits.append(f"JS errors {snapshot.get('js_error_count')}")
    if snapshot.get("current_contains_after_text") is True:
        bits.append("предложенный текст уже найден на странице")
    if snapshot.get("current_contains_before_text") is False:
        bits.append("старый текст из ревью уже не найден")
    if snapshot.get("freshness_warning"):
        bits.append("вывод: старое ревью, нужна перепроверка")
    return f"{prefix}: " + "; ".join(bits[:8]) if bits else prefix


def _format_dt(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_date(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_decimal_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _format_extra(extra: dict[str, Any] | None) -> str:
    if not isinstance(extra, dict) or not extra:
        return ""
    rendered: list[str] = []
    for key, value in list(extra.items())[:6]:
        if value is None or value == "":
            continue
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        rendered.append(f"{key}={text}")
    return ", ".join(rendered)


def _format_competitor_flags(item: dict[str, Any]) -> str:
    labels = [
        ("has_price", "цены"),
        ("has_booking_cta", "бронь/CTA"),
        ("has_reviews", "отзывы"),
        ("has_phone", "телефон"),
        ("has_telegram", "Telegram"),
        ("has_whatsapp", "WhatsApp"),
    ]
    return ", ".join(
        f"{label}: {'да' if item.get(key) else 'нет'}"
        for key, label in labels
    )


def _format_competitor_evidence(evidence: Any) -> str:
    if not isinstance(evidence, dict):
        return ""
    bits: list[str] = []
    queries = evidence.get("queries")
    if isinstance(queries, list) and queries:
        bits.append("запросы: " + ", ".join(f"«{q}»" for q in queries[:3]))
    if evidence.get("competitor_domain"):
        bit = f"конкурент {evidence.get('competitor_domain')}"
        if evidence.get("competitor_position"):
            bit += f" на позиции {evidence.get('competitor_position')}"
        bits.append(bit)
    if evidence.get("site_position"):
        bits.append(f"наша позиция {evidence.get('site_position')}")
    if evidence.get("feature"):
        bits.append(f"признак {evidence.get('feature')}")
    if evidence.get("schema_type"):
        bits.append(f"schema {evidence.get('schema_type')}")
    competitors_with = evidence.get("competitors_with")
    if isinstance(competitors_with, list) and competitors_with:
        bits.append("есть у: " + ", ".join(map(str, competitors_with[:5])))
    if evidence.get("competitor_url"):
        bits.append(f"URL {evidence.get('competitor_url')}")
    return "; ".join(bits[:5])


def _format_plan_block(plan: Plan) -> str:
    """The current plan, by title + severity. The LLM should refer
    owners to these rather than invent new actions."""
    if not plan.actions:
        return (
            "ТЕКУЩИЙ ПЛАН: пусто (срочных действий не найдено или модули "
            "пока не запущены)."
        )
    parts = ["ТЕКУЩИЙ ПЛАН (направляй к этим действиям, не выдумывай новые):"]
    for a in plan.actions:
        parts.append(
            f"  - [{a.severity}] {a.title} → {a.link_to}",
        )
    return "\n".join(parts)


# ── Public API ───────────────────────────────────────────────────────


def build_user_message(
    *,
    domain: str,
    target_config: dict[str, Any],
    understanding: dict[str, Any],
    snap: BrainSnapshot,
    plan: Plan,
    history: list[dict[str, str]],
    new_message: str,
    mode: str = "answer",
    long_term_memory: list[str] | None = None,
    battle_plan_seed: str | None = None,
) -> str:
    """Compose the single user-message string for `call_plain`."""
    mode = _normalise_mode(mode)
    # Strategic focus, if owner has set one, takes the top-of-prompt
    # slot — every answer must be subordinated to it (the prompt
    # itself spells out the rule).
    from app.core_audit.brain.memory import format_memory_block
    from app.core_audit.strategic_focus import (
        from_target_config,
        render_for_prompt,
    )
    focus = from_target_config(target_config or {})

    blocks = [
        "КОНТЕКСТ — это всё, что ты знаешь про сайт. Все ответы должны "
        "опираться только на этот блок. Если факта тут нет — его нет.",
        "",
        _format_mode_block(mode),
        "",
        render_for_prompt(focus),
        "",
        _format_business_block(
            domain=domain,
            target_config=target_config,
            understanding=understanding,
        ),
        "",
        _format_full_snapshot(snap),
        "",
        _format_plan_block(plan),
        "",
    ]

    # Long-term memory: things this owner said in PREVIOUS conversations.
    # Helps the assistant feel like a returning helper instead of a
    # fresh stranger at every new chat.
    memory_block = format_memory_block(long_term_memory or [])
    if memory_block:
        blocks.append(memory_block)
        blocks.append("")

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
    # Battle-plan seed: deterministic plan rendered from facts. The
    # LLM should re-shape it under the owner's actual ask (короче,
    # только индексация, без E-E-A-T, etc.) — but invent NO new
    # actions or numbers beyond what's already in this seed.
    if battle_plan_seed and mode == "battle_plan":
        blocks.append(
            "ОСНОВА БОЕВОГО ПЛАНА (фактологическая, сгенерирована "
            "детерминистски из снапшота — все факты в ней реальны, "
            "ничего не выдумано):"
        )
        blocks.append(battle_plan_seed)
        blocks.append("")

    blocks.append(f"ВЛАДЕЛЕЦ СЕЙЧАС СПРАШИВАЕТ: {new_message.strip()}")
    blocks.append("")
    blocks.append(
        "Ответь по существу, опираясь только на КОНТЕКСТ. "
        "Дай чуть больше деталей: факт → вывод → следующий шаг → где "
        "проверить. Если данных не хватает — скажи об этом честно.",
    )
    if mode == "battle_plan":
        blocks.append(
            "Собери боевой SEO-план. Обязательный формат: "
            "1) факты, на которых строишь план; 2) план из максимум "
            "5 действий; 3) что проверить после выполнения; 4) каких "
            "данных не хватает. У каждого действия должны быть: "
            "страница/модуль, причина, конкретная правка, ожидаемый "
            "эффект без обещания позиций, проверка результата. "
            "Не обещай гарантированный топ-5.",
        )
    if "индекс" in new_message.lower() or "webmaster" in new_message.lower():
        blocks.append(
            "Для вопроса про индексацию сначала отдели: что точно видно "
            "по данным, что может мешать по найденным признакам, и что "
            "ещё надо проверить. Не называй неизвестный статус ошибкой.",
        )
    if "конкур" in new_message.lower() or "соперник" in new_message.lower():
        blocks.append(
            "Для вопроса про конкурентов ответь слоями: кто реально "
            "виден в выдаче; какие SEO-признаки deep-dive увидел у них "
            "и у нас; какие возможности роста уже посчитаны; чего в "
            "данных пока нет и где это проверить: /studio/competitors.",
        )
    return "\n".join(blocks)


def free_chat(
    *,
    domain: str,
    target_config: dict[str, Any],
    understanding: dict[str, Any],
    snap: BrainSnapshot,
    plan: Plan,
    history: list[dict[str, str]],
    new_message: str,
    mode: str = "answer",
    long_term_memory: list[str] | None = None,
    battle_plan_seed: str | None = None,
) -> dict[str, Any]:
    """One-turn chat. Returns
        {reply: str | None, proposal: dict | None,
         cost_usd, model, input_tokens, output_tokens}.

    Two mutually-exclusive happy paths:
      - LLM answers with text → reply is set, proposal is None.
      - LLM calls propose_strategic_focus → proposal is set with the
        full focus payload + rationale, reply may be None or a short
        accompanying note. The caller (endpoint) returns both to the
        frontend; the UI shows a modal «Применить фокус?» when
        proposal is non-null. NOTHING is written to DB until the
        owner confirms via POST .../strategic-focus/from-proposal.
    """
    new_message = (new_message or "").strip()
    if not new_message:
        raise ValueError("empty message")
    mode = _normalise_mode(mode)
    if len(new_message) > MAX_USER_MESSAGE_CHARS:
        new_message = new_message[:MAX_USER_MESSAGE_CHARS] + " […обрезано]"

    user_msg = build_user_message(
        domain=domain,
        target_config=target_config,
        understanding=understanding,
        snap=snap,
        plan=plan,
        history=history or [],
        new_message=new_message,
        mode=mode,
        long_term_memory=long_term_memory,
        battle_plan_seed=battle_plan_seed,
    )

    out, usage = call_with_optional_tools(
        model_tier="cheap",
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tools=[PROPOSE_FOCUS_TOOL],
        max_tokens=_max_tokens_for_mode(mode),
    )

    proposal: dict[str, Any] | None = None
    tu = out.get("tool_use")
    if tu and tu.get("name") == "propose_strategic_focus":
        raw = tu.get("input") or {}
        # Coerce shapes the API model expects. Drop empty fields so
        # frontend doesn't render «—» chips for nothing.
        proposal = {
            "label": str(raw.get("label") or "").strip(),
            "products": _ensure_str_list(raw.get("products")),
            "regions": _ensure_str_list(raw.get("regions")),
            "query_signals": _ensure_str_list(raw.get("query_signals")),
            "deprioritised": _ensure_str_list(raw.get("deprioritised")),
            "exit_criterion": (raw.get("exit_criterion") or "").strip() or None,
            "owner_note": (raw.get("owner_note") or "").strip() or None,
            "deadline": None,
            "rationale": (raw.get("rationale") or "").strip(),
        }

    text = (out.get("text") or "").strip() or None
    return {
        "reply": text,
        "proposal": proposal,
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "model": usage.get("model") or "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        # Surface stop_reason so the endpoint can flag truncated
        # answers in the API response and the UI can warn the owner.
        # Without this the partial answer gets saved as if it were
        # complete and feeds back into the next turn's history.
        "truncated": bool(usage.get("truncated")),
        "stop_reason": usage.get("stop_reason") or "",
    }


def _ensure_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
    return out


def _normalise_mode(mode: str | None) -> str:
    value = (mode or "answer").strip().lower()
    return value if value in CHAT_MODES else "answer"


def _max_tokens_for_mode(mode: str) -> int:
    if mode == "battle_plan":
        return MAX_BATTLE_PLAN_REPLY_TOKENS
    if mode == "discussion":
        return MAX_DISCUSSION_REPLY_TOKENS
    return MAX_REPLY_TOKENS


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_REPLY_TOKENS",
    "MAX_DISCUSSION_REPLY_TOKENS",
    "MAX_BATTLE_PLAN_REPLY_TOKENS",
    "MAX_USER_MESSAGE_CHARS",
    "SYSTEM_PROMPT",
    "build_user_message",
    "free_chat",
]
